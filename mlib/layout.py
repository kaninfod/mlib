"""Naming rules — the layout standard, codified (design §4).

Folder:  <artist tag>/<album tag>/
File:    NN <title tag>.mp3     NN = zero-padded track number (2 digits, 3 when >99)

P0 scope: sanitize / target computation / layout checks (read-only detection).
Rename planning + execution arrives with apply (P2).
"""
from __future__ import annotations

import re
import unicodedata


def sanitize(name: str) -> str:
    """Make a string safe as a filesystem name: NFC-normalize, replace
    filesystem-hostile chars and control chars with '_', collapse repeats,
    trim spaces and trailing dots."""
    name = unicodedata.normalize("NFC", str(name)) if name is not None else ""
    name = re.sub(r'[/\\:*?"<>|\x00-\x1f]', "_", name)
    name = re.sub(r"_{2,}", "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.rstrip(".")
    return name or "_"


def pad_track(track: int) -> str:
    """Zero-padded track number: 2 digits, 3 digits above 99."""
    return f"{track:03d}" if track > 99 else f"{track:02d}"


def format_file_target(track: int | None, title: str | None, ext: str = ".mp3") -> str | None:
    """Target filename for a track, or None when track/title unknown."""
    if track is None or title is None or not str(title).strip():
        return None
    return f"{pad_track(track)} {sanitize(title)}{ext}"


def album_path_target(artist: str | None, album: str | None) -> str | None:
    """Target folder path '<artist>/<album>', or None when either is unknown."""
    if not artist or not album:
        return None
    return f"{sanitize(artist)}/{sanitize(album)}"


def conforms_to_layout(filename: str, track: int | None, title: str | None) -> bool:
    """True when an existing filename already equals its layout target."""
    target = format_file_target(track, title)
    return target is not None and filename == target


def natural_key(s: str) -> list:
    """Sort key treating digit runs numerically: 2 < 10."""
    parts = re.split(r"(\d+)", s)
    return [int(p) if p.isdigit() else p.lower() for p in parts]