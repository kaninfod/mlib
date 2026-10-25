"""infer — suggestions from filenames and paths for MISSING tags only.

Design: tags on disk always win; inference fills holes (current is null).
Suggestions land prefilled-in `new` with an `inferred: true` marker — the
human sees them (amber, rejectable) in the editor; apply writes them like
any other decision; nothing writes without having been displayed.

Strictness: ambiguity is never resolved by guessing (the Doxy sessions-box
shape — multiple dash-separated tokens, numeric-leading candidates —
produces no suggestion at all; the human decides there).
"""
from __future__ import annotations

import re

INBOX_TERMS = {"downloads", "download", "new_music", "new", "inbox",
               "staging", "import_staging", "incoming", "to_import"}
DASH = r"\s*[–-]\s*"

# filename: leading track number + optional separators, then a title
_FILENAME = re.compile(r"^\s*0*?(\d{1,3})\s*(?:[-._)\]\s]|$)\s*(.*)$")

# folder: 'YYYY - Artist - Album' (year must be space-padded before its dash,
# so year-RANGES like '1937-1946 - …' don't masquerade as years)
_FOLDER_WITH_YEAR = re.compile(rf"^(19|20)\d{{2}}{DASH}(?P<b>.*)$")
# folder: 'Artist - Album' (no year); artist must not be all-numbers
_FOLDER_NO_YEAR = re.compile(rf"^(?P<a>[^.]*?){DASH}(?P<al>[^.]+)$")


def from_filename(stem: str) -> tuple[int | None, str | None]:
    """'01 - She's Fresh' -> (1, "She's Fresh"); 'She's Fresh' -> (None, …)."""
    m = _FILENAME.match(stem.strip())
    if not m:
        return None, stem.strip() or None
    n = int(m.group(1))
    title = m.group(2).strip()
    # a bare number with no title: the number is the title, not the track
    if not title:
        return None, str(n)
    return n, title


def from_path_folder(folder_name: str) -> tuple[int | None, str | None, str | None]:
    """Parse a single folder NAME into (year, artist, album); all-null when
    the shape is ambiguous (and the year only counts when the WHOLE pattern
    parses — a stray year next to an unparseable remainder is a guess)."""
    name = folder_name.strip()
    m = _FOLDER_WITH_YEAR.match(name)
    if m:
        year = int(m.group(0)[:4])
        pair = _split_artist_album(m.group("b"))
        if pair:
            artist, album = pair
            return year, artist, album
        return None, None, None     # year-range masquerade: nothing at all
    pair = _split_artist_album(name)
    if pair:
        artist, album = pair
        return None, artist, album
    return None, None, None


def _split_artist_album(rest: str) -> tuple[str, str] | None:
    """Split 'Artist - Album' on the FIRST dash separator; None when the
    artist half is (mostly) numbers or either half empty (callers treat
    None as 'no parse', never as a parsed pair of Nones)."""
    m = re.split(rf"{DASH}", rest, maxsplit=1)
    if len(m) != 2:
        return None
    artist, album = m[0].strip(), m[1].strip()
    if not artist or not album:
        return None
    if re.fullmatch(r"[\d\s–\-.,()]*", artist):   # '1937-46'-style: no artist
        return None
    return artist, album


def from_path(parts: list[str]) -> tuple[str | None, str | None]:
    """Depth-based inference off the relative album path: 'artist/album'
    (the layout standard itself, e.g. new_music/<artist>/<album>), or one
    inbox term deep: 'downloads/<artist>/<album>' (the Bowie shape)."""
    parts = [p for p in parts if p and p.strip()]
    if not parts:
        return None, None
    if parts[0].strip().lower() in INBOX_TERMS:
        parts = parts[1:]
    if len(parts) == 2:
        a, al = parts[0].strip(), parts[1].strip()
        if a and al:
            return a, al
    return None, None