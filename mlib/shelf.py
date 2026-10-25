"""shelf — apply pending work, then move album folders into a collection
root (design §2 / §8 flow). The staging→collection move-in.

Mechanics:
- applies pending plan work first (unless fully converged already)
- moves each `<rootA>/<artist>/<album>` folder into `<rootB>/<artist>/<album>`
  — same filesystem → atomic rename; different (Mac→NAS) → rsync + verify
  (file count) then remove the source: a verified move, never a blind copy
- target collisions are skipped with a report (the user decides)
- optional --rescan triggers gonic afterwards
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import apply as apply_mod
from . import convert
from . import model


def _move_same_device(src: Path, dst: Path) -> None:
    os.rename(src, dst)


def _move_cross_device(src: Path, dst: Path) -> None:
    """rsync then verify then remove source — resumable + verified move."""
    subprocess.run(["rsync", "-a", f"{src}{os.sep}", str(dst)], check=True,
                   capture_output=True)
    src_files = [p for p in src.rglob("*") if p.is_file()]
    dst_files = [p for p in dst.rglob("*") if p.is_file()]
    if len(src_files) != len(dst_files) or sum(p.stat().st_size for p in src_files) \
            != sum(p.stat().st_size for p in dst_files):
        raise RuntimeError(f"move verify failed — source kept: {src}")
    shutil.rmtree(src)


def shelf(plan: dict, from_root: Path, into_root: Path,
          dry_run: bool = False, only: set[str] | None = None,
          keep_going: bool = False, bitrate: str = None,
          rescan: bool = False) -> dict:
    """Apply pending work, then move the plan's album folders into a root."""
    from_root, into_root = Path(from_root), Path(into_root)
    if not into_root.is_dir():
        raise RuntimeError(f"--into root does not exist: {into_root}")
    bitrate = bitrate or convert.BITRATE_DEFAULT

    moves: list[dict] = []
    # 1) apply pending (real work only when not dry-run)
    results, changed = apply_mod.run_apply(plan, from_root, write=not dry_run,
                                           only=only, keep_going=keep_going,
                                           bitrate=bitrate)
    apply_report = {"results": results, "changed": changed}

    # 2) move the album folders (the plan's post-state locations)
    for alb in plan.get("albums", []):
        rel = alb["path"]
        if only and rel not in only:
            continue
        # only converged albums move: rel must equal the layout target of
        # single-valued artist/album — un-converged stays in staging (D8)
        from . import apply as _a
        from . import layout as _layout
        artist = _a._resolved(alb.get("artist", {}))
        album_n = _a._resolved(alb.get("album", {}))
        target = _layout.album_path_target(
            str(artist) if artist is not None else None,
            str(album_n) if album_n is not None else None)
        if target is None or target != rel:
            moves.append({"album": rel,
                          "state": "skipped (not converged — decide in editor, "
                                   "apply, then shelf)"})
            continue
        src = from_root / rel
        dst = into_root / rel
        entry = {"album": rel, "state": None}
        if not src.is_dir():
            entry["state"] = "skipped (folder missing)"
        elif dst.exists():
            entry["state"] = "skipped (already in collection)"
        elif dry_run:
            entry["state"] = "would move"
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if os.stat(src).st_dev == os.stat(dst.parent).st_dev:
                _move_same_device(src, dst)
            else:
                _move_cross_device(src, dst)
            entry["state"] = "moved"
        moves.append(entry)

    # 3) gonic refresh
    rescanned = None
    if rescan and not dry_run:
        from . import rescan as rescan_mod
        try:
            rescanned = rescan_mod.gonic_rescan()
        except Exception as e:  # noqa: BLE001 — non-fatal, reported
            rescanned = f"rescan failed: {e}"

    return {"apply": apply_report, "moves": moves,
            "dry_run": dry_run, "rescan": rescanned}


def render_shelf(out: dict) -> str:
    lines = ["shelf" + (" (dry-run)" if out["dry_run"] else "") + ":"]
    a = out["apply"]
    applied = sum(1 for r in a["results"] if r["result"] == "applied")
    lines.append(f"  apply: {applied} converged" +
                 ("" if a["changed"] else " (nothing pending)"))
    moved = sum(1 for m in out["moves"] if m.get("state") == "moved")
    skipped = sum(1 for m in out["moves"] if m.get("state", "").startswith("skipped"))
    lines.append(f"  moves: {moved} moved · {skipped} skipped")
    for m in out["moves"]:
        mark = {"moved": "✔", "would move": "→", None: ""}.get(m["state"], " ! ")
        lines.append(f"    {mark} {m['album']}" +
                     (f" — {m['state']}" if m.get("state") else ""))
    if out.get("rescan"):
        lines.append(f"  rescan: {out['rescan']}")
    return "\n".join(lines)