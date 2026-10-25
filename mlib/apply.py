"""apply — converge files toward the plan (design §7, D8/D9).

Per album, stage order (D9): convert → tags → cover → rename files →
folder (clean-in-place) → junk → verify → audit + refresh the album entry
in the plan (via a mini re-catalog of the final folder).

Clean-in-place mechanics (D8):
- converted files are NEW files landing in the computed `artist/album`
  folder at the root of the tree; originals never move — a folder with
  non-mp3 audio remaining is deliberately NOT renamed (it stays an inbox
  husk; the converged mp3s + cover move out).
- pure-mp3 folders are renamed as a unit when their path does not match
  the standard.
- albums with unresolved artist/album (mixed or missing values) are
  skipped entirely — the tool never invents metadata; the editor decides.
Everything is previewable, idempotent (skip-if-already-correct everywhere),
and recorded in the plan's `_applied` per album.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timezone
from pathlib import Path

from . import catalog as catalog_mod
from . import convert
from . import layout
from . import tags
from .catalog import AUDIO_EXTS, IMAGE_EXTS
from .tags import CANONICAL_COVER


def is_junk(name: str) -> bool:
    return name.startswith("._") or name in {".DS_Store", "Thumbs.db", "desktop.ini"}


# ---------------------------------------------------------------- ops model

@dataclass
class AlbumOps:
    album: str                                  # plan path (current, rel to root)
    src_dir: Path
    target_rel: str | None = None
    target_abs: Path | None = None
    tracks: list[dict] = dc_field(default_factory=list)
    final_names: list[str | None] = dc_field(default_factory=list)
    converts: list[tuple[Path, Path]] = dc_field(default_factory=list)
    tag_checks: list[tuple[Path, dict]] = dc_field(default_factory=list)
    tag_diffs: int = 0                # files with at least one tag delta
    renames: list[tuple[Path, Path]] = dc_field(default_factory=list)
    cover_ops: list[str] = dc_field(default_factory=list)
    folder_strategy: str | None = None          # in-place | rename | materialize | None
    junk: list[Path] = dc_field(default_factory=list)
    skipped: str | None = None
    errors: list[str] = dc_field(default_factory=list)

    @property
    def has_ops(self) -> bool:
        return bool(self.converts or self.renames or self.junk or self.tag_diffs
                    or self.folder_strategy not in (None, "in-place"))


# ---------------------------------------------------------------- helpers

def _resolved(field: dict):
    """One decided value for a field, else None (never invent)."""
    if field.get("new") is not None:
        v = field["new"]
        return v if isinstance(v, (str, int)) and v != "" else None
    v = field.get("current")
    return v if isinstance(v, (str, int)) else None


def _is_unresolved(field: dict) -> bool:
    return (isinstance(field.get("current"), list) and field.get("new") is None) \
        or _resolved(field) is None


# ---------------------------------------------------------------- ops builder

def build_ops(alb: dict, root: Path) -> AlbumOps:
    src = root / alb["path"]
    tracks = alb.get("tracks", [])
    ops = AlbumOps(album=alb["path"], src_dir=src, tracks=tracks, final_names=[])

    if not src.is_dir():
        ops.errors.append(f"album folder missing on disk: {src}")
        ops.skipped = "folder missing"
        return ops

    artist = _resolved(alb.get("artist", {}))
    album_n = _resolved(alb.get("album", {}))
    unresolved = [n for n in ("artist", "album") if _is_unresolved(alb.get(n, {}))]
    if unresolved:
        ops.errors.append("album fields unresolved: " + ", ".join(unresolved)
                          + " — decide in the editor; nothing will be written")
        ops.skipped = "album fields unresolved"
        _plan_cover_probe(ops)
        return ops

    target_rel = layout.album_path_target(str(artist), str(album_n))
    if target_rel is None:
        ops.errors.append("artist/album missing — no folder target")
        ops.skipped = "no folder target"
        return ops
    ops.target_rel = target_rel
    ops.target_abs = root / target_rel
    if src.resolve() == ops.target_abs.resolve():
        ops.folder_strategy = "in-place"
    else:
        ops.folder_strategy = "pending"         # rename vs materialize, below

    # tracktotal auto-fill (D7): unset for all → the album's track count
    auto_total = next((t["tracktotal"].get("current") for t in tracks
                       if t["tracktotal"].get("current") is not None), None)
    needs_auto = auto_total is None and bool(tracks) and any(
        t["tracktotal"].get("current") is None and t["tracktotal"].get("new") is None
        for t in tracks)
    if needs_auto:
        auto_total = len(tracks)

    # per track: convert → tag → final name
    names: list[str | None] = []
    for i, trk in enumerate(tracks):
        spath = src / trk["file"]
        title = _resolved(trk["title"])
        ntr = _resolved(trk["track"])
        ntt = _resolved(trk["tracktotal"])
        if ntt is None and needs_auto:
            ntt = auto_total
        final = layout.format_file_target(ntr, title)
        names.append(final)

        if spath.suffix.lower() == ".mp3":
            if not spath.exists():
                ops.errors.append(f"missing file: {trk['file']}")
                names[-1] = None
                continue
            work = spath
        else:
            if not spath.exists():
                ops.errors.append(f"missing file: {trk['file']}")
                names[-1] = None
                continue
            work = src / convert.tmp_convert_name(i, spath)
            ops.converts.append((spath, work))

        f: dict = {}
        if title is not None:
            f["title"] = title
        if ntr is not None or ntt is not None:
            f["track"] = ntr
            f["tracktotal"] = ntt
        for key, val in (("artist", artist), ("album", album_n),
                         ("albumartist", _resolved(alb.get("albumartist", {}))),
                         ("year", _resolved(alb.get("year", {}))),
                         ("genre", _resolved(alb.get("genre", {})))):
            if val is not None:
                f[key] = val
        ops.tag_checks.append((work, f))
        # skip-if-correct pre-check (cheap read): count real deltas
        if work.exists() and work.suffix.lower() == ".mp3":
            cur = tags.id3_fields(work)
            differs = False
            for k, v in f.items():
                want = int(v) if k in ("year", "track", "tracktotal") else str(v)
                if cur.get(k) != want:
                    differs = True
                    break
        else:
            differs = True          # fresh conversion: every field differs
        if differs:
            ops.tag_diffs += 1

        if final:
            if work != src / final:
                ops.renames.append((work, src / final))
        else:
            ops.errors.append(f"cannot name conversion of '{trk['file']}': "
                              f"missing track number or title (edit the plan)")

    finals = [n for n in names if n]
    if len(set(finals)) != len(finals):
        dupes = sorted({n for n in finals if finals.count(n) > 1})
        ops.errors.append("name collision — two tracks produce the same target "
                          "name: " + "; ".join(dupes)
                          + " (fix titles/track numbers in the editor)")
        # atomic skip: the collision must be resolved by the human first
        ops.renames.clear()
        ops.tag_checks.clear()
        ops.tag_diffs = 0
        ops.cover_ops.clear()
        ops.junk.clear()
        ops.folder_strategy = None
        return ops
    ops.final_names = names

    # folder strategy (clean-in-place)
    if ops.folder_strategy == "pending":
        disk_audio = [p for p in src.iterdir() if p.suffix.lower() in AUDIO_EXTS]
        nonmp3 = [p for p in disk_audio if p.suffix.lower() != ".mp3"]
        if ops.target_abs.exists():
            ops.errors.append(f"target folder already exists: {ops.target_abs} "
                              f"— resolve in the editor (e.g. adjust the album tag)")
            ops.skipped = "folder target exists"
            ops.folder_strategy = None
        elif nonmp3:
            ops.folder_strategy = "materialize"   # originals stay behind (D8)
        else:
            ops.folder_strategy = "rename"

    _plan_cover_probe(ops)
    ops.junk = sorted(p for p in src.iterdir() if is_junk(p.name))
    # a late skip (folder target exists) keeps the album fully untouched —
    # atomicity: the user resolves the conflict first, then re-runs
    if ops.skipped:
        ops.converts.clear()
        ops.renames.clear()
        ops.tag_checks.clear()
        ops.cover_ops.clear()
        ops.junk.clear()
    return ops


def _plan_cover_probe(ops: AlbumOps) -> None:
    """Which cover actions this album needs (executed only on write)."""
    src = ops.src_dir
    if not src.is_dir():
        return
    images = sorted(p for p in src.iterdir()
                    if p.suffix.lower() in IMAGE_EXTS and not is_junk(p.name))
    n_mp3 = sum(1 for trk in ops.tracks if trk["ext"] == ".mp3") + len(ops.converts)
    exact = next((p for p in images if p.name == CANONICAL_COVER), None)
    if exact is not None:
        if n_mp3:
            ops.cover_ops.append(f"embed into {n_mp3} mp3(s)")
        return
    ci = next((p for p in images if p.name.casefold() == CANONICAL_COVER), None)
    if ci is not None:
        ops.cover_ops.append(f"rename '{ci.name}' -> cover.jpg (case)")
    elif images:
        ops.cover_ops.append(f"normalize '{images[0].name}' -> cover.jpg")
    else:
        for trk in ops.tracks:
            p = src / trk["file"]
            if p.exists():
                data = tags.embedded_picture_bytes(p)
                if data:
                    ops.cover_ops.append(
                        f"extract {max(1, len(data) // 1024)} KB art from "
                        f"'{trk['file']}'")
                    break
    if ops.cover_ops and n_mp3:
        ops.cover_ops.append(f"embed into {n_mp3} mp3(s)")


# ---------------------------------------------------------------- summary

def describe(ops: AlbumOps) -> list[str]:
    lines: list[str] = []
    for e in ops.errors:
        lines.append(f"  ! {e}")
    if ops.skipped and not (ops.converts or ops.renames or ops.tag_checks):
        lines.append(f"  · skipped: {ops.skipped}")
        return lines
    if ops.converts:
        exts = sorted({p.suffix.lstrip('.') for p, _ in ops.converts})
        lines.append(f"  · convert {len(ops.converts)} file(s) "
                     f"({', '.join(exts)}) -> mp3 320 CBR")
    if ops.tag_diffs:
        lines.append(f"  · tags: update {ops.tag_diffs} file(s) "
                     f"(skip-if-correct; tracktotal auto-filled where unset)")
    if ops.renames:
        s, d = ops.renames[0]
        more = f" +{len(ops.renames) - 1} more" if len(ops.renames) > 1 else ""
        lines.append(f"  · rename files: '{s.name}' -> '{d.name}'{more}")
    if ops.folder_strategy == "rename":
        lines.append(f"  · folder rename: '{ops.album}' -> '{ops.target_rel}'")
    elif ops.folder_strategy == "materialize":
        lines.append(f"  · folder: materialize '{ops.target_rel}' — move mp3s + "
                     f"cover; originals stay in '{ops.album}'")
    for op in ops.cover_ops:
        lines.append(f"  · cover: {op}")
    if ops.junk:
        lines.append(f"  · junk: delete {len(ops.junk)} file(s)")
    if not lines:
        lines.append("  · already conforms — nothing to do")
    return lines


# ---------------------------------------------------------------- executor

def _final_mp3_for(ops: AlbumOps, idx: int) -> Path:
    """The mp3 a plan track maps to after conversion + file renames (in src)."""
    trk = ops.tracks[idx]
    fname = ops.final_names[idx] if idx < len(ops.final_names) else None
    if fname:
        return ops.src_dir / fname
    if trk["ext"] == ".mp3":
        return ops.src_dir / trk["file"]
    return ops.src_dir / convert.tmp_convert_name(idx, ops.src_dir / trk["file"])


def _two_phase_rename(renames: list[tuple[Path, Path]]) -> None:
    """Safe even when a target name equals another source's name."""
    staged: list[tuple[Path, Path]] = []
    for s, d in renames:
        tmp = s.with_name(f".__mlibmv_{d.name}")
        os.rename(s, tmp)
        staged.append((tmp, d))
    for tmp, d in staged:
        os.rename(tmp, d)


def _ffmpeg_png_to_jpg(png: Path, jpg: Path) -> None:
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(png),
                    "-q:v", "2", str(jpg)], check=True, capture_output=True)


def execute(ops: AlbumOps, alb: dict, root: Path,
            bitrate: str = convert.BITRATE_DEFAULT) -> dict:
    rec: dict = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "album": ops.album, "to": ops.target_rel}
    src = ops.src_dir

    # 2) convert
    if ops.converts:
        for s, d in ops.converts:
            convert.convert_file(s, d, bitrate=bitrate)
        rec["converted"] = len(ops.converts)

    # 3) tags (skip-if-correct inside; verify-after-write in stage 8)
    tagged_files = 0
    written_fields = 0
    for path, fields in ops.tag_checks:
        if path.exists():
            written, _ = tags.write_tag_fields(path, **fields)
            if written:
                tagged_files += 1
                written_fields += len(written)
    rec["tagged_files"] = tagged_files
    rec["written_fields"] = written_fields

    # 4) cover — materialize cover.jpg, then embed in every mp3
    cover_path = src / CANONICAL_COVER
    for op in ops.cover_ops:
        if op.startswith("rename '"):
            # case-only rename: EXISTS() lies on case-insensitive filesystems —
            # find the real entry by listing, then rename it to the exact name
            old = next((p for p in src.iterdir()
                        if p.name.casefold() == CANONICAL_COVER
                        and p.name != CANONICAL_COVER), None)
            if old is not None:
                os.rename(old, src / CANONICAL_COVER)
                rec.setdefault("cover", []).append(f"renamed {old.name}")
        elif op.startswith("normalize '"):
            old = src / op.split("'")[1]
            if old.exists() and not cover_path.exists():
                if old.suffix.lower() in (".jpg", ".jpeg"):
                    shutil.move(str(old), str(cover_path))
                else:
                    _ffmpeg_png_to_jpg(old, cover_path)
                rec.setdefault("cover", []).append(f"normalized {old.name}")
        elif op.startswith("extract "):
            for trk in ops.tracks:
                p = src / trk["file"]
                if not p.exists():
                    continue
                data = tags.embedded_picture_bytes(p)
                if data:
                    if tags.sniff_mime(data) == "image/png":
                        tmp = src / "__mlibcover.png"
                        tmp.write_bytes(data)
                        _ffmpeg_png_to_jpg(tmp, cover_path)
                        tmp.unlink(missing_ok=True)
                    else:
                        cover_path.write_bytes(data)
                    rec.setdefault("cover", []).append(f"extracted from {trk['file']}")
                    break
    n_embed = 0
    if cover_path.exists():
        cbytes = cover_path.read_bytes()
        mime = tags.sniff_mime(cbytes)
        for path, _f in ops.tag_checks:
            if path.exists() and path.suffix.lower() == ".mp3":
                if tags.embed_picture(path, cbytes, mime):
                    n_embed += 1
    if n_embed:
        rec["cover_embedded"] = n_embed

    # 5) rename files (collision-safe)
    if ops.renames:
        _two_phase_rename(ops.renames)
        rec["renamed_files"] = len(ops.renames)

    # 6) folder (clean-in-place)
    final_dir = src
    if ops.folder_strategy == "rename":
        if ops.target_abs.exists():
            raise RuntimeError(f"target folder exists: {ops.target_abs}")
        ops.target_abs.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, ops.target_abs)
        final_dir = ops.target_abs
    elif ops.folder_strategy == "materialize":
        final_dir = ops.target_abs
        final_dir.mkdir(parents=True, exist_ok=True)
        movers = []
        for i in range(len(ops.tracks)):
            mp3 = _final_mp3_for(ops, i)
            if mp3.exists():
                dst = final_dir / mp3.name
                if dst.exists():
                    raise RuntimeError(f"target exists: {dst}")
                movers.append((mp3, dst))
        cover = src / CANONICAL_COVER
        if cover.exists():
            movers.append((cover, final_dir / CANONICAL_COVER))
        for m, d in movers:
            shutil.move(str(m), str(d))
    if ops.folder_strategy in ("rename", "materialize"):
        rec["folder"] = ops.folder_strategy
        rec["folder_to"] = ops.target_rel

    # 7) junk (both the final folder and any husk left behind)
    n_junk = 0
    for d in {final_dir, src}:
        if d.is_dir():
            for p in sorted(d.iterdir()):
                if is_junk(p.name):
                    p.unlink()
                    n_junk += 1
    if n_junk:
        rec["junk_removed"] = n_junk
    if ops.folder_strategy == "materialize" and src.is_dir():
        rec["left_in_husk"] = sorted(p.name for p in src.iterdir())

    # 8) verify: files exist + tags landed
    problems = verify_album(ops, alb, final_dir)
    rec["verified"] = not problems
    rec["verify_problems"] = problems or None
    return rec


def verify_album(ops: AlbumOps, alb: dict, final_dir: Path) -> list[str]:
    problems: list[str] = []
    artist = _resolved(alb.get("artist", {}))
    album_n = _resolved(alb.get("album", {}))
    yr = _resolved(alb.get("year", {}))
    gn = _resolved(alb.get("genre", {}))
    aa = _resolved(alb.get("albumartist", {}))
    for idx in range(len(ops.tracks)):
        p = final_dir / _final_mp3_for(ops, idx).name
        if not p.exists():
            problems.append(f"missing after apply: {ops.tracks[idx]['file']}")
            continue
        got = tags.id3_fields(p)
        trk = ops.tracks[idx]
        want_title = _resolved(trk["title"])
        want_trk = _resolved(trk["track"])
        want_tt = _resolved(trk["tracktotal"])
        checks: dict = {}
        if want_title is not None:
            checks["title"] = str(want_title)
        if artist is not None:
            checks["artist"] = str(artist)
        if album_n is not None:
            checks["album"] = str(album_n)
        if aa is not None:
            checks["albumartist"] = str(aa)
        if yr is not None:
            checks["year"] = int(yr)
        if gn is not None:
            checks["genre"] = str(gn)
        if want_trk is not None:
            checks["track"] = int(want_trk)
        if want_tt is not None:
            checks["tracktotal"] = int(want_tt)
        for k, want in checks.items():
            gotv = got.get(k)
            if gotv is None or gotv != want:
                problems.append(f"{p.name}: tag '{k}' is '{gotv}', expected '{want}'")
    return problems


# ---------------------------------------------------------------- orchestration

def apply_album(alb: dict, root: Path, write: bool,
                bitrate: str = convert.BITRATE_DEFAULT,
                dismissed: list[dict] | None = None
                ) -> tuple[AlbumOps, dict | None]:
    """Build + (optionally) execute ops for one album.

    Returns (ops, record) where record is:
      None                 — preview only
      {"nochange": True}   — nothing to do
      {"error": msg}       — execution failed (album left as-is/safe)
      fresh album dict     — success: the album's post-state, with _applied
    """
    ops = build_ops(alb, root)
    if not write:
        return ops, None
    if not ops.has_ops:
        return ops, {"nochange": True, "album": alb["path"]}
    try:
        rec = execute(ops, alb, root, bitrate=bitrate)
    except Exception as e:  # noqa: BLE001 — surfaced to the CLI, album stays safe
        ops.errors.append(f"apply failed: {e}")
        return ops, {"error": str(e), "album": alb["path"]}
    final_dir = (ops.target_abs if ops.folder_strategy in ("rename", "materialize")
                 else ops.src_dir)
    mp3s = sorted(p for p in final_dir.iterdir() if p.suffix.lower() == ".mp3")
    images = sorted(p for p in final_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    others = sorted(p for p in final_dir.iterdir()
                    if p.suffix.lower() not in AUDIO_EXTS | IMAGE_EXTS
                    and not is_junk(p.name))
    fresh = catalog_mod._assemble_album(final_dir.relative_to(root).as_posix(), mp3s, root,
                                        image_names=[p.name for p in images],
                                        other_names=[p.name for p in others],
                                        dismissed=dismissed)
    fresh["_uid"] = alb.get("_uid", "")
    applied = list(alb.get("_applied", []))
    applied.append(rec)
    fresh["_applied"] = applied
    return ops, fresh


def run_apply(plan: dict, root: Path, write: bool = False,
              only: set[str] | None = None, keep_going: bool = False,
              bitrate: str = convert.BITRATE_DEFAULT) -> tuple[list[dict], bool]:
    """Apply a plan against a root.

    Returns (results, changed). On write the plan dict is updated in place
    (applied albums replaced by their refreshed post-state); the CALLER owns
    saving the plan file.
    """
    results: list[dict] = []
    changed = False
    albums = plan.get("albums", [])
    dismissed = plan.get("_meta", {}).get("dismissed", [])
    for idx, alb in enumerate(albums):
        if only and alb.get("path") not in only:
            continue
        ops, outcome = apply_album(alb, root, write=write, bitrate=bitrate,
                                   dismissed=dismissed)
        entry: dict = {"album": alb["path"], "preview": describe(ops),
                       "skipped": ops.skipped, "errors": list(ops.errors)}
        if outcome is None:
            entry["result"] = "preview"
        elif outcome.get("nochange"):
            entry["result"] = "nochange"
        elif outcome.get("error"):
            entry["result"] = "error"
            entry["error"] = outcome["error"]
            results.append(entry)
            if not keep_going:
                return results, changed   # albums applied so far still count
        else:
            entry["result"] = "applied"
            rec = dict(outcome["_applied"][-1])
            rec.pop("ts", None)
            entry["applied"] = rec
            albums[idx] = outcome   # plan now reflects post-apply truth
            changed = True
        results.append(entry)
    return results, changed


def print_results(results: list[dict], write: bool) -> str:
    lines: list[str] = []
    applied = sum(1 for r in results if r["result"] == "applied")
    nochange = sum(1 for r in results if r["result"] == "nochange")
    errors = sum(1 for r in results if r["result"] == "error")
    skipped = sum(1 for r in results if r.get("skipped"))
    head = ("Applied" if write else "Preview") + \
        f": {applied} applied · {nochange} already-conform · {skipped} skipped" \
        + (f" · {errors} error(s)" if errors else "")
    lines.append(head)
    for r in results:
        lines.append("")
        lines.append(f"{r['album']}")
        lines.extend(r["preview"])
        if r["result"] == "applied":
            a = r.get("applied", {})
            bits = []
            for k in ("converted", "renamed_files", "cover_embedded",
                      "junk_removed"):
                if a.get(k):
                    bits.append(f"{k.replace('_', ' ')}: {a[k]}")
            if a.get("folder_to"):
                bits.append(f"folder -> {a['folder_to']}")
            if a.get("left_in_husk") is not None:
                bits.append(f"husk retains {len(a['left_in_husk'])} file(s)")
            if a.get("verify_problems"):
                bits.append(f"VERIFY ISSUES: {len(a['verify_problems'])}")
                for p in a["verify_problems"][:4]:
                    lines.append(f"      - {p}")
            if bits:
                lines.append("  ✔ " + " · ".join(bits))
        elif r["result"] == "error":
            lines.append(f"  ✗ {r['error']}")
    return "\n".join(lines)