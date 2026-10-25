"""Tag reading via mutagen — P0 exposes the read side only.

Handles: mp3 (ID3), m4a (MP4 atoms), flac/ogg/opus (Vorbis comments),
wma (ASF), wav (ID3-in-wav); webm containers expose duration only.
The write side arrives with apply (P2), in the same module.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from pathlib import Path

import mutagen
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.wave import WAVE
from mutagen.asf import ASF
from mutagen.flac import FLAC

AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".opus", ".ogg", ".wma", ".wav", ".webm"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
CANONICAL_COVER = "cover.jpg"


@dataclass
class TrackInfo:
    ext: str
    title: str | None = None
    track: int | None = None
    tracktotal: int | None = None
    artist: str | None = None
    albumartist: str | None = None
    album: str | None = None
    year: int | None = None
    genres: list[str] = dc_field(default_factory=list)
    duration: float | None = None
    embedded_cover: bool = False


# ---------------------------------------------------------------- parsing utils

def parse_track_string(value) -> tuple[int | None, int | None]:
    """'8' / '8/17' / 8 -> (8, 17) (total may be None)."""
    if value is None:
        return None, None
    s = str(value).strip()
    m = re.match(r"^(\d+)(?:\s*/\s*(\d+))?$", s)
    if not m:
        return None, None
    track = int(m.group(1)) if m.group(1) else None
    total = int(m.group(2)) if m.group(2) else None
    return track, total


def parse_year(value) -> int | None:
    """First 4-digit run in a date-ish string ('2008', '2008-05-01')."""
    if value is None:
        return None
    m = re.search(r"\b(\d{4})\b", str(value))
    return int(m.group(1)) if m else None


def _first(tag_value) -> str | None:
    """First non-empty string from a tag value (str or list)."""
    if tag_value is None:
        return None
    values = tag_value if isinstance(tag_value, list) else [tag_value]
    for v in values:
        s = str(v).strip() if v is not None else ""
        if s:
            return s
    return None


def _all(tag_value) -> list[str]:
    if tag_value is None:
        return []
    values = tag_value if isinstance(tag_value, list) else [tag_value]
    out = []
    for v in values:
        s = str(v).strip() if v is not None else ""
        if s and s not in out:
            out.append(s)
    return out


def _vc_get(tags, key: str):
    try:
        return tags.get(key)
    except Exception:
        return None


# ---------------------------------------------------------------- readers

def read_audio(path: Path) -> TrackInfo | None:
    """Read one audio file; None when unreadable/unsupported (catalog flags it)."""
    try:
        m = mutagen.File(str(path))
    except Exception:
        return None
    if m is None:
        return None

    info = TrackInfo(ext=Path(path).suffix.lower())
    try:
        info.duration = getattr(getattr(m, "info", None), "length", None)
    except Exception:
        info.duration = None

    try:
        if isinstance(m, (MP3, WAVE)) or (m.tags is not None and m.tags.__class__.__name__ == "ID3"):
            _read_id3(m, info)
        elif isinstance(m, MP4):
            _read_mp4(m, info)
        else:
            _read_vc_style(m, info)
    except Exception:
        return info  # salvage what duration told us

    return info


def _read_id3(m, info: TrackInfo) -> None:
    tags = m.tags
    if not tags:
        return
    # NB: use getall — ID3.get misses frames whose HashKey is suffixed
    # (e.g. "APIC:Cover (front)")
    def txt(fid):
        frames = tags.getall(fid)
        return frames[0].text if frames else None

    info.title = _first(txt("TIT2"))
    info.artist = _first(txt("TPE1"))
    # ffmpeg-tagged files carry albumartist as TXXX:albumartist instead of TPE2
    info.albumartist = _first(txt("TPE2")) or _first(txt("TXXX:albumartist"))
    info.album = _first(txt("TALB"))
    info.year = parse_year(_first(txt("TDRC")) or _first(txt("TYER")))
    track, total = parse_track_string(_first(txt("TRCK")))
    info.track, info.tracktotal = track, total
    info.genres = _all(txt("TCON"))
    info.embedded_cover = bool(tags.getall("APIC"))


def _read_mp4(m, info: TrackInfo) -> None:
    tags = m.tags
    if not tags:
        return
    g = lambda key: tags.get(key)
    info.title = _first(g("\xa9nam"))
    info.artist = _first(g("\xa9ART"))
    info.albumartist = _first(g("aART"))
    info.album = _first(g("\xa9alb"))
    info.year = parse_year(_first(g("\xa9day")))
    info.genres = _all(g("\xa9gen") or g("gnre"))
    trkn = g("trkn")
    if isinstance(trkn, list) and trkn and isinstance(trkn[0], (list, tuple)):
        pair = trkn[0]
        info.track = int(pair[0]) if pair and pair[0] else None
        info.tracktotal = int(pair[1]) if pair and len(pair) > 1 and pair[1] else None
    info.embedded_cover = g("covr") is not None


def _read_vc_style(m, info: TrackInfo) -> None:
    """FLAC / OggOpus / OggVorbis / ASF — dict-shaped tags."""
    tags = getattr(m, "tags", None)
    if tags is None:
        # webm etc: container audio without readable vc-style tags
        return
    grab = {k: _vc_get(tags, k) for k in (
        "title", "artist", "albumartist", "album", "date", "genre",
        "tracknumber", "tracktotal", "totaltracks", "discnumber")}
    if isinstance(m, ASF):  # ASF uses its own key names
        grab = {**grab, **{k: _vc_get(tags, k) for k in (
            "WM/AlbumArtist", "WM/AlbumTitle", "WM/Year", "WM/Genre",
            "WM/Track", "WM/TrackNumber", "WM/TrackCount")}}
    info.title = _first(grab.get("title"))
    info.artist = _first(grab.get("artist"))
    info.albumartist = _first(grab.get("albumartist") or grab.get("WM/AlbumArtist"))
    info.album = _first(grab.get("album") or grab.get("WM/AlbumTitle"))
    info.year = parse_year(_first(grab.get("date") or grab.get("WM/Year")))
    info.genres = _all(grab.get("genre") or grab.get("WM/Genre"))
    track, total = parse_track_string(_first(grab.get("tracknumber") or grab.get("WM/Track") or grab.get("WM/TrackNumber")))
    info.track, info.tracktotal = track, total
    if info.tracktotal is None:
        _, total2 = parse_track_string(_first(grab.get("tracktotal") or grab.get("totaltracks") or grab.get("WM/TrackCount")))
        info.tracktotal = total2
    info.embedded_cover = any(
        _vc_get(tags, k) for k in ("metadata_block_picture", "coverart")) \
        or getattr(m, "pictures", None) not in (None, []) \
        or (isinstance(m, ASF) and _vc_get(tags, "WM/Picture") is not None)