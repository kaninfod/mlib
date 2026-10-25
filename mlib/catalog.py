"""catalog — scan a music root and produce the plan JSON (the contract).

Walks <root>, groups audio files by album folder, reads tags, computes the
album-level current values (with tagtool's mixed-field semantics), per-track
records + layout targets, data-quality issues (missing tags, mixed fields,
off-layout names/folders, non-mp3, cover state, duplicates).
"""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import layout, model, tags
from . import infer
from .model import new_field, PLAN_NAME
from .tags import AUDIO_EXTS, IMAGE_EXTS, CANONICAL_COVER, TrackInfo

DUPE_DURATION_TOLERANCE = 1.0


def _walk(root: Path):
    """Group files under root: audio / images / others per album dir (posix rel)."""
    audio: dict[str, list[Path]] = defaultdict(list)
    images: dict[str, list[Path]] = defaultdict(list)
    others: dict[str, list[Path]] = defaultdict(list)

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            if fn.startswith(".") or fn in (PLAN_NAME, PLAN_NAME + ".bak",
                                            PLAN_NAME.replace(".json", ".tmp.json")):
                continue
            rel_dir = p.relative_to(root).parent.as_posix()
            if rel_dir == ".":
                rel_dir = ""
            ext = p.suffix.lower()
            if ext in AUDIO_EXTS:
                audio[rel_dir].append(p)
            elif ext in IMAGE_EXTS:
                images[rel_dir].append(p)
            else:
                others[rel_dir].append(p)
    return audio, images, others


def _field_from(values: list) -> dict:
    """Distinct-ordered values -> current (str | list+mixed | None)."""
    distinct = []
    for v in values:
        if v is not None and str(v).strip() and v not in distinct:
            distinct.append(v)
    if not distinct:
        return new_field(None)
    if len(distinct) == 1:
        return new_field(distinct[0])
    field = new_field(list(distinct))
    field["mixed"] = True
    return field


def _mixed_issue(name: str, field: dict) -> str | None:
    cur = field.get("current")
    if not isinstance(cur, list):
        return None
    shown = [str(x) for x in cur[:3]]
    if len(cur) > 3:
        shown.append(f"… (+{len(cur) - 3})")
    return f"tag: mixed {name}: {' / '.join(shown)}"


def _missing_issue(name: str, count: int, total: int) -> str | None:
    if count == total:
        return f"tag: {name} missing on all {total} tracks"
    if count:
        return f"tag: {name} missing on {count}/{total} tracks"
    return None


def _dupes(infos: list[tuple[str, TrackInfo]]) -> list[str]:
    """Same title + (near-)same duration, different files -> report pairs."""
    by_title: dict[str, list[tuple[str, TrackInfo]]] = defaultdict(list)
    for fname, ti in infos:
        if ti.title:
            key = " ".join(ti.title.casefold().split())
            by_title[key].append((fname, ti))
    out = []
    for key, group in sorted(by_title.items()):
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                (fa, ta), (fb, tb) = group[i], group[j]
                if ta.duration is not None and tb.duration is not None \
                        and abs(ta.duration - tb.duration) <= DUPE_DURATION_TOLERANCE:
                    out.append(f"dupe: '{ta.title}' — {fa} == {fb}")
    return out


def _layout_issues(reldir: str, fields: dict[str, dict],
                   track_pairs: list[tuple[str, TrackInfo]]) -> list[str]:
    issues = []
    parts = [p for p in reldir.split("/") if p]
    if len(parts) != 2:
        issues.append(f"layout: path depth {len(parts) or '(root)'} "
                      f"(expected artist/album)")
    target_dir = layout.album_path_target(fields["artist"].get("current"),
                                          fields["album"].get("current"))
    if target_dir and target_dir != reldir and isinstance(fields["artist"].get("current"), str):
        issues.append(f"layout: folder rename pending ('{reldir}' -> '{target_dir}')")
    total = len(track_pairs)
    off = []
    for fname, ti in track_pairs:
        target = layout.format_file_target(ti.track, ti.title)
        if target is not None and target != fname:
            off.append((fname, target))
    if total and off:
        example = f" ('{off[0][0]}' -> '{off[0][1]}')" if off else ""
        more = f" +{len(off) - 1} more" if len(off) > 1 else ""
        issues.append(f"layout: {len(off)}/{total} files off-layout{example}{more}")
    return issues


def _cover_state(image_names: list[str], infos: list[tuple[str, TrackInfo]]) -> tuple[dict, list[str]]:
    """Returns (cover dict, issues)."""
    issues = []
    canonical = next((n for n in image_names
                      if n.casefold() == CANONICAL_COVER), None)
    cover = {
        "file": canonical,
        "other_images": [n for n in image_names if n != canonical],
        "embedded_in_count": sum(1 for _, ti in infos if ti.embedded_cover),
        "action": None,
    }
    if canonical is None:
        if cover["other_images"]:
            issues.append(f"cover: no cover.jpg "
                          f"(have '{cover['other_images'][0]}' -> normalize pending)")
        elif cover["embedded_in_count"]:
            issues.append(f"cover: art embedded in {cover['embedded_in_count']} "
                          f"track(s) -> extract pending")
        else:
            issues.append("cover: missing")
    return cover, issues


def _format_issues(infos: list[tuple[str, TrackInfo]]) -> list[str]:
    counts: dict[str, int] = defaultdict(int)
    issues = []
    for fname, ti in infos:
        if ti.ext != ".mp3":
            counts[ti.ext] += 1
        if ti.duration is None and ti.title is None and ti.artist is None:
            issues.append(f"format: unreadable '{fname}'")
    for ext, n in sorted(counts.items()):
        issues.append(f"format: {n} {ext.lstrip('.')} -> convert pending")
    return issues


def _assemble_album(reldir: str, files: list[Path], root: Path,
                    image_names: list[str], other_names: list[str]) -> dict:
    infos: list[tuple[str, TrackInfo]] = []
    for p in files:
        ti = tags.read_audio(p)
        if ti is not None:
            infos.append((p.name, ti))

    vals = {name: [getattr(ti, name) for _, ti in infos] for name in
            ("artist", "albumartist", "album", "year")}
    genre_vals: list = []
    for _, ti in infos:
        genre_vals.extend(ti.genres)
    fields = {
        "artist": _field_from(vals["artist"]),
        "albumartist": _field_from(vals["albumartist"]),
        "album": _field_from(vals["album"]),
        "year": _field_from(vals["year"]),
        "genre": _field_from(genre_vals),
    }

    # ---- inference: fill holes from folder/file names (tags always win) ----
    path_year, pattern_artist, pattern_album = infer.from_path_folder(
        reldir.rsplit("/", 1)[-1])
    depth_artist, depth_album = infer.from_path(reldir.split("/"))
    artist_guess = pattern_artist or depth_artist
    album_guess = pattern_album or depth_album
    for name, guess in (("artist", artist_guess), ("album", album_guess),
                        ("year", path_year)):
        f = fields[name]
        if guess is None or f.get("current") is not None:
            continue          # nothing to infer, or the tag already knows
        fields[name] = {"current": None, "new": int(guess)
                        if name == "year" else guess, "inferred": True}

    issues: list[str] = []
    # M2: minimum tags on every file
    for label in ("artist", "album", "title", "year", "genre", "track", "tracktotal"):
        if label == "genre":
            missing = sum(1 for _, ti in infos if not ti.genres)
        else:
            missing = sum(1 for _, ti in infos if getattr(ti, label) is None)
        if missing:
            msg = _missing_issue(label, missing, len(infos))
            if msg:
                issues.append(msg)
    for name in model.ALBUM_FIELDS:
        mi = _mixed_issue(name, fields[name])
        if mi:
            issues.append(mi)

    cover, cover_issues = _cover_state(image_names, infos)
    issues.extend(_layout_issues(reldir, fields, infos))
    issues.extend(_format_issues(infos))
    issues.extend(_dupes(infos))
    issues.extend(cover_issues)

    tracks = []
    for fname, ti in infos:
        # per-track suggestion fill: title/track from the filename when
        # the corresponding tag is missing (tags always win)
        if_track, if_title = infer.from_filename(Path(fname).stem)
        title_field = new_field(ti.title)
        if ti.title is None and if_title:
            title_field = {"current": None, "new": if_title, "inferred": True}
        track_field = new_field(ti.track)
        if ti.track is None and if_track is not None:
            track_field = {"current": None, "new": if_track, "inferred": True}
        tracks.append({
            "file": fname,
            "ext": ti.ext,
            "dur": round(ti.duration, 1) if ti.duration else None,
            "title": title_field,
            "track": track_field,
            "tracktotal": new_field(ti.tracktotal),
            "target_file": layout.format_file_target(
                model.effective(track_field), model.effective(title_field)),
        })
    tracks.sort(key=lambda t: (model.effective(t["track"])
                if model.effective(t["track"]) is not None else 10_000,
                layout.natural_key(t["file"])))

    return {
        "path": reldir,
        **fields,
        "cover": cover,
        "tracks": tracks,
        "issues": issues,
        "_uid": model.album_uid(reldir),
        "other_files": other_names,
    }


def build_plan(root: Path) -> dict:
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"not a directory: {root}")
    audio, images, others = _walk(root)

    plan = model.new_plan(root)
    plan["_meta"]["created"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    dirs = sorted(set(audio) | set(images) | set(others), key=layout.natural_key)
    for reldir in dirs:
        if not audio.get(reldir):
            continue  # image-only / junk-only folders carry no plan entries
        album = _assemble_album(
            reldir, audio[reldir], root,
            image_names=[p.name for p in images.get(reldir, [])],
            other_names=[p.name for p in others.get(reldir, [])],
        )
        plan["albums"].append(album)

    plan["_summary"] = model.plan_counts(plan)
    return plan