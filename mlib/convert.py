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