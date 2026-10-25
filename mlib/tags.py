"""Tag read + write via mutagen.

Read side: mp3 (ID3), m4a (MP4 atoms), flac/ogg/opus (Vorbis comments),
wma (ASF), wav (ID3-in-wav); webm containers expose duration only.
Write side (P2): the plan converges mp3 files — always load-then-modify
(a fresh ID3() + save WIPES existing frames), write only changed frames,
v2.4 UTF-8 by default. The cover layer extracts/normalizes/embeds.
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
from mutagen.id3 import (ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK, APIC)

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

# ================================================================= write side

def _norm_txt(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _frame_text(tags, fid: str) -> str | None:
    frames = tags.getall(fid)
    if not frames:
        return None
    # frame.text is a LIST of strings — str() of it would give "['x']"
    return _norm_txt(_first(getattr(frames[0], "text", None)))


def _id3_file(path: Path):
    """Load an mp3 ensuring ID3 exists — load-then-modify, never a blind wipe."""
    m = MP3(str(path))
    existed = m.tags is not None
    if m.tags is None:
        m.add_tags()
    return m, m.tags, existed


def id3_fields(path: Path) -> dict:
    """Read back the fields we write (for skip-if-correct + verification)."""
    m, tags, _ = _id3_file(path)
    track_s = _frame_text(tags, "TRCK")
    track, total = parse_track_string(track_s) if track_s else (None, None)
    return {
        "title": _frame_text(tags, "TIT2"),
        "artist": _frame_text(tags, "TPE1"),
        "album": _frame_text(tags, "TALB"),
        "albumartist": _frame_text(tags, "TPE2"),
        "year": parse_year(_frame_text(tags, "TDRC")),
        "genre": _frame_text(tags, "TCON"),
        "track": track,
        "tracktotal": total,
    }


def write_tag_fields(path: Path, *, title=None, artist=None, album=None,
                     albumartist=None, year=None, genre=None,
                     track=None, tracktotal=None) -> tuple[list[str], list[str]]:
    """Converge the mp3's frames toward the given fields.

    None = leave existing untouched (never invent). Returns (written, kept)
    lists of field names for reporting. Only saves when something changed.
    """
    m, tags, _ = _id3_file(path)
    written: list[str] = []
    cur = id3_fields(path)

    if title is not None and cur["title"] != _norm_txt(title):
        tags.add(TIT2(encoding=3, text=[str(title)])); written.append("title")
    if artist is not None and cur["artist"] != _norm_txt(artist):
        tags.add(TPE1(encoding=3, text=[str(artist)])); written.append("artist")
    if album is not None and cur["album"] != _norm_txt(album):
        tags.add(TALB(encoding=3, text=[str(album)])); written.append("album")
    if albumartist is not None and cur["albumartist"] != _norm_txt(albumartist):
        tags.add(TPE2(encoding=3, text=[str(albumartist)])); written.append("albumartist")
    if year is not None and cur["year"] != int(year):
        tags.add(TDRC(encoding=3, text=[str(int(year))])); written.append("year")
    if genre is not None and cur["genre"] != _norm_txt(genre):
        tags.add(TCON(encoding=3, text=[str(genre)])); written.append("genre")
    if track is not None or tracktotal is not None:
        want_t = int(track) if track is not None else None
        want_tt = int(tracktotal) if tracktotal is not None else None
        if want_t is not None:
            want_s = f"{want_t}/{want_tt}" if want_tt else str(want_t)
            if cur["track"] != want_t or (want_tt and cur["tracktotal"] != want_tt):
                tags.add(TRCK(encoding=3, text=[want_s])); written.append("track")
        elif want_tt is not None:
            # never invent a track number: only restate an existing one
            base_num = cur["track"]
            if base_num is not None and cur["tracktotal"] != want_tt:
                tags.add(TRCK(encoding=3, text=[f"{base_num}/{want_tt}"]))
                written.append("tracktotal")

    if written:
        tags.save(str(path))
    return written, []


def extract_picture(path: Path) -> tuple[bytes, str] | None:
    """Pull embedded art (mp3 APIC / flac Picture / mp4 covr). None if absent."""
    try:
        if path.suffix.lower() == ".flac":
            f = FLAC(str(path))
            if f.pictures:
                return f.pictures[0].data, (f.pictures[0].mime or "image/jpeg")
        if path.suffix.lower() == ".mp3":
            m, tags, _ = _id3_file(path)
            aps = tags.getall("APIC")
            if aps:
                return aps[0].data, (aps[0].mime or "image/jpeg")
        if path.suffix.lower() == ".m4a":
            m = MP4(str(path))
            covr = (m.tags or {}).get("covr")
            if covr:
                return bytes(covr[0]), "image/jpeg"
    except Exception:
        return None
    return None


def _is_jpeg(data: bytes) -> bool:
    return data[:3] == b"\xff\xd8\xff"


def _is_png(data: bytes) -> bool:
    return data[:8] == b"\x89PNG\r\n\x1a\n"


def sniff_mime(data: bytes) -> str:
    if _is_png(data):
        return "image/png"
    return "image/jpeg"


def embedded_picture_bytes(path: Path) -> bytes | None:
    """Return the embedded art bytes matching the canonical cover mime gate."""
    pic = extract_picture(path)
    if not pic:
        return None
    return pic[0]


def embed_picture(path: Path, data: bytes, mime: str) -> bool:
    """Ensure the mp3 carries this art (APIC type 3). Skip when identical."""
    m, tags, _ = _id3_file(path)
    aps = tags.getall("APIC")
    for ap in aps:
        if ap.data == data:
            return False
    # replace non-matching art with the canonical one (single APIC discipline)
    tags.delall("APIC")
    tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover (front)", data=data))
    tags.save(str(path))
    return True
