"""The plan JSON — the contract between catalog, editor and apply (design §3).

Fields use tagtool's proven current/new convention:
    {"current": <disk truth>, "new": null-or-value}

When tracks in one album disagree on an album field, "current" is a LIST of
distinct values and "mixed" is true — the field's tagtool-style semantics,
generalized beyond genre.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

PLAN_NAME = "mlib_plan.json"
PLAN_VERSION = 2

ALBUM_FIELDS = ("artist", "albumartist", "album", "year", "genre")
TRACK_FIELDS = ("title", "track", "tracktotal")


class PlanError(Exception):
    """Raised for unreadable/invalid plan files."""


# ---------------------------------------------------------------- field dicts

def new_field(current) -> dict:
    return {"current": current, "new": None}


def effective(field: dict):
    """The value apply will write: the decision (new) if set, else current."""
    if field.get("new") is not None:
        return field["new"]
    return field.get("current")


def pending(field: dict) -> bool:
    """A field is pending when a decision differs from disk truth."""
    new = field.get("new")
    return new is not None and new != field.get("current")


# ---------------------------------------------------------------- plan file I/O

def album_uid(path: str) -> str:
    """Stable short id for an album path — survives re-catalog merges."""
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:10]


def new_plan(root: Path) -> dict:
    return {
        "_meta": {
            "tool": "mlib",
            "v": PLAN_VERSION,
            "root": str(root),
            "created": None,  # filled by catalog (iso timestamp)
        },
        "albums": [],
        "_summary": {},
    }


def load_plan(path: Path) -> dict:
    """Load and sanity-check a plan file."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise PlanError(f"cannot read plan {path}: {e}") from e
    except json.JSONDecodeError as e:
        raise PlanError(f"{path} is not valid JSON: {e}") from e
    _check(data, path)
    return data


def _check(data, path: Path) -> None:
    if not isinstance(data, dict):
        raise PlanError(f"{path}: plan must be a JSON object")
    meta = data.get("_meta")
    if not isinstance(meta, dict) or meta.get("tool") != "mlib":
        raise PlanError(f"{path}: missing _meta.tool == 'mlib' (not an mlib plan?)")
    if meta.get("v") != PLAN_VERSION:
        raise PlanError(f"{path}: plan version {meta.get('v')} unsupported "
                        f"(expected {PLAN_VERSION})")
    if not isinstance(data.get("albums"), list):
        raise PlanError(f"{path}: 'albums' must be a list")
    for i, alb in enumerate(data["albums"]):
        if not isinstance(alb, dict) or "path" not in alb or not isinstance(alb.get("tracks"), list):
            raise PlanError(f"{path}: albums[{i}] lacks 'path'/'tracks'")


def save_plan(path: Path, data: dict, backup: bool = True) -> None:
    """Atomic write; rotates the previous file to <name>.bak."""
    path = Path(path)
    tmp = path.with_suffix(".tmp" + path.suffix)
    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- change helpers

def plan_counts(plan: dict) -> dict:
    albums = plan.get("albums", [])
    tracks = sum(len(a.get("tracks", [])) for a in albums)
    genres: set = set()
    issues = 0
    edits = 0
    albums_with_edits = 0
    for alb in albums:
        g = alb.get("genre", {}).get("current")
        if isinstance(g, list):
            genres.update(x for x in g if x)
        elif g:
            genres.add(g)
        issues += len(alb.get("issues", []))
        n = len(pending_changes(alb))
        if n:
            edits += n
            albums_with_edits += 1
    return {
        "albums": len(albums),
        "tracks": tracks,
        "genres": len(genres),
        "issues": issues,
        "edits": edits,
        "albums_with_edits": albums_with_edits,
    }


def pending_field(field: dict, name: str) -> dict | None:
    if pending(field):
        return {"field": name, "current": field.get("current"), "new": field.get("new")}
    return None


def pending_changes(album: dict) -> list[dict]:
    """All pending edits of one album, album-level and track-level."""
    rows: list[dict] = []
    for name in ALBUM_FIELDS:
        row = pending_field(album.get(name, {}), name)
        if row:
            rows.append({"album": album["path"], **row})
    cover_action = album.get("cover", {}).get("action")
    if cover_action:
        rows.append({"album": album["path"], "field": "cover.action",
                     "current": None, "new": cover_action})
    for trk in album.get("tracks", []):
        for name in TRACK_FIELDS:
            row = pending_field(trk.get(name, {}), name)
            if row:
                rows.append({"album": album["path"], "file": trk.get("file"), **row})
    return rows


def pending_albums(plan: dict) -> list[str]:
    return [a["path"] for a in plan.get("albums", []) if pending_changes(a)]

# ---------------------------------------------------------------- rescan merge

def _merge_field(old: dict, fresh: dict) -> dict:
    """Carry a decision from the previous plan into a fresh catalog entry."""
    if old.get("new") is not None:
        merged = dict(fresh)
        merged["new"] = old["new"]
        return merged
    return fresh


def _same_track_key(trk: dict) -> tuple:
    cur = trk.get("track", {}).get("current")
    return (cur if cur is not None else 10_000, trk.get("file", ""))


def merge_album_pending(old_album: dict, fresh_album: dict) -> dict:
    """Carry the human's pending `new` decisions from old into fresh (matched
    tracks by track number, fallback positional). Mutates and returns fresh."""
    for name in ALBUM_FIELDS:
        if name in old_album and name in fresh_album:
            fresh_album[name] = _merge_field(old_album[name], fresh_album[name])
    if old_album.get("cover", {}).get("action"):
        fresh_album.setdefault("cover", {})["action"] = old_album["cover"]["action"]
    # audit history survives re-catalogs when the album didn't move
    if old_album.get("_applied") and old_album.get("path") == fresh_album.get("path"):
        fresh_album["_applied"] = old_album["_applied"]
    old_by_key = sorted(old_album.get("tracks", []), key=_same_track_key)
    fresh_by_key = sorted(fresh_album.get("tracks", []), key=_same_track_key)
    for ot, ft in zip(old_by_key, fresh_by_key):
        for name in TRACK_FIELDS:
            ft[name] = _merge_field(ot.get(name, {}), ft.get(name, {}))
    return fresh_album


def merge_rescan(old_plan: dict, fresh_plan: dict) -> dict:
    """Merge a fresh catalog with the previous plan's pending decisions,
    matched by album _uid. Unmatched albums enter as-is (new arrivals or
    post-apply locations). Returns the merged plan (fresh_plan mutated)."""
    old_by_uid = {a["_uid"]: a for a in old_plan.get("albums", [])
                  if a.get("_uid")}
    for alb in fresh_plan.get("albums", []):
        old = old_by_uid.get(alb.get("_uid", ""))
        if old:
            merge_album_pending(old, alb)
    fresh_plan["_meta"]["merged_from"] = old_plan.get("_meta", {}).get("created")
    return fresh_plan
