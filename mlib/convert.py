"""convert — ffmpeg wrapper: flac/opus/webm → mp3 320 kbps CBR (design §6).

The engine is shared by the `convert` command (P3) and apply stage 2 (P2).
Rules (D2/D4, §13): originals are never moved, rewritten or deleted; the
produced mp3 carries source tags via -map_metadata; embedded art is NOT
carried by ffmpeg (covered by the cover stage instead). Output is verified
by re-reading with mutagen.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import mutagen

from . import tags

BITRATE_DEFAULT = "320k"


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): "
                           f"{proc.stderr.strip()[:400]}")


def run_ffmpeg(src: Path, dst: Path, bitrate: str = BITRATE_DEFAULT) -> None:
    """Convert one audio file to mp3 at the target bitrate (dst must be .mp3)."""
    _run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src),
          "-map_metadata", "0", "-vn",
          "-codec:a", "libmp3lame", "-b:a", bitrate, str(dst)])


def convert_file(src: Path, dst: Path, bitrate: str = BITRATE_DEFAULT) -> tuple[Path, float]:
    """Convert src → dst (mp3) safely and verify the result.

    Writes through a temp file in dst's folder, verifies with mutagen, then
    renames into place. Returns (dst, duration_seconds).
    """
    dst = Path(dst)
    if dst.suffix.lower() != ".mp3":
        raise ValueError(f"converter produces mp3 only, got {dst.name}")
    if not ffmpeg_available():
        raise RuntimeError("ffmpeg not found on PATH (mlib doctor)")
    tmp = dst.parent / f".__mlibcv__{dst.stem}.mp3"
    run_ffmpeg(src, tmp, bitrate=bitrate)
    try:
        m = mutagen.File(str(tmp))
        dur = getattr(m.info, "length", 0) or 0
        if dur <= 0:
            raise RuntimeError(f"converted file failed verification: {tmp.name}")
    except mutagen.MutagenError as e:
        raise RuntimeError(f"converted file failed verification: {tmp.name} ({e})")
    import os
    os.replace(tmp, dst)
    return dst, round(dur, 1)


def tmp_convert_name(i: int, src: Path) -> str:
    """Temp name for a conversion whose final filename is not yet known
    (renamed by the layout stage). NOT dot-prefixed (the junk cleaner only
    removes dotfiles — conversions must survive junk cleanup) and the
    __mlibcv marker cannot collide with real files."""
    return f"__mlibcv{i:03d} {src.stem}.mp3"

# ==================================================== standalone command part

def _cover_source_for(folder: Path) -> Path | None:
    """First original file whose embedded art we can pull (after conversion)."""
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() in (".flac", ".m4a", ".mp3", ".opus", ".ogg"):
            if tags.embedded_picture_bytes(p):
                return p
    return None


def convert_root(root: Path, bitrate: str = BITRATE_DEFAULT,
                 dry_run: bool = False, remove_originals: bool = False
                 ) -> list[dict]:
    """Standalone conversion, no plan needed (design §2/§6/D3).

    Converts flac/opus/ogg/webm → mp3 320 CBR IN PLACE (same folder), carries
    source tags, and materializes cover.jpg when a folder has no cover file
    but its sources carry art. Originals stay unless --remove-originals is
    explicitly passed (nothing is ever deleted implicitly).
    Returns per-file report dicts.
    """
    from mlib.catalog import AUDIO_EXTS, IMAGE_EXTS
    from mlib.tags import CANONICAL_COVER
    src_targets = {".flac", ".opus", ".ogg", ".webm"}   # D3 set
    report: list[dict] = []
    folders_done: set[Path] = set()
    for p in sorted(Path(root).rglob("*")):
        if not p.is_file() or p.suffix.lower() not in src_targets:
            continue
        if is_hidden_junk(p):
            continue
        dst = p.with_suffix(".mp3")
        entry = {"src": p.name, "dst": dst.name, "folder": p.parent.name,
                 "action": None, "seconds": None}
        if dst.exists():
            entry["action"] = f"skipped (dst exists)"
            report.append(entry)
            continue
        if dry_run:
            entry["action"] = "would convert"
            report.append(entry)
            continue
        _, seconds = convert_file(p, dst, bitrate=bitrate)
        entry["action"] = "converted"
        entry["seconds"] = seconds
        if remove_originals:
            p.unlink()          # explicit user instruction (D4 escape hatch)
            entry["removed_original"] = True
        report.append(entry)
        folders_done.add(p.parent)

    # after conversion, cover.jpg for folders that have none at all
    if not dry_run:
        folders = {Path(root)} | folders_done
        for folder in list(folders):
            if not folder.is_dir():
                continue
            images = [q for q in folder.iterdir()
                      if q.suffix.lower() in IMAGE_EXTS
                      and not is_hidden_junk(q)]
            if any(q.name == CANONICAL_COVER or
                   q.name.casefold() == CANONICAL_COVER for q in images):
                continue
            if images:
                continue        # some image exists; leave naming to curation
            src = _cover_source_for(folder)
            if src is None:
                continue
            data = tags.embedded_picture_bytes(src)
            if data is None:
                continue
            cover = folder / CANONICAL_COVER
            if tags.sniff_mime(data) == "image/png":
                tmp = folder / "__mlibcover.png"
                tmp.write_bytes(data)
                subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error",
                                "-i", str(tmp), "-q:v", "2", str(cover)],
                               check=True, capture_output=True)
                tmp.unlink(missing_ok=True)
            else:
                cover.write_bytes(data)
            report.append({"src": src.name, "dst": CANONICAL_COVER,
                           "folder": folder.name, "action": "cover extracted",
                           "seconds": None})
    return report


def is_hidden_junk(p: Path) -> bool:
    import os
    name = p.name
    return name.startswith(".") and name not in (".", "..") \
        or name.startswith("._")
