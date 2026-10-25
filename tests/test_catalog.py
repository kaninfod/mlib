"""catalog assembly tests — pure logic, no binary fixtures needed
(tags.read_audio is patched; its parsing is covered by test_parse_track_string etc.)."""
from unittest.mock import patch

from mlib.catalog import _assemble_album
from mlib.tags import TrackInfo


def _info(title, track, total=None, artist="Adele", album="19", year=2008,
          genres=("Pop",), dur=250.0, ext=".mp3", embedded=False):
    return TrackInfo(
        ext=ext, title=title, track=track, tracktotal=total, artist=artist,
        albumartist=artist, album=album, year=year, genres=list(genres),
        duration=dur, embedded_cover=embedded)


def _assemble(tmp_path, reldir, infos_by_name, images=(), others=()):
    paths = [tmp_path / name for name in infos_by_name]
    with patch("mlib.catalog.tags.read_audio", side_effect=lambda p: infos_by_name[p.name]):
        return _assemble_album(reldir, paths, tmp_path,
                               list(images), list(others))


def test_conformant_album_has_no_issues(tmp_path):
    infos = {
        "01 Daydreamer.mp3": _info("Daydreamer", 1, 2),
        "02 Best for Last.mp3": _info("Best for Last", 2, 2),
    }
    alb = _assemble(tmp_path, "Adele/19", infos, images=["cover.jpg"])
    assert alb["issues"] == []
    assert alb["artist"]["current"] == "Adele"
    assert alb["album"]["current"] == "19"
    assert alb["year"]["current"] == 2008
    assert alb["genre"]["current"] == "Pop"
    assert alb["tracks"][0]["target_file"] == "01 Daydreamer.mp3"
    assert alb["cover"]["file"] == "cover.jpg"


def test_canonical_cover_silences_image_issues(tmp_path):
    infos = {"01 Daydreamer.mp3": _info("Daydreamer", 1, 12)}
    alb = _assemble(tmp_path, "Adele/19", infos, images=["cover.jpg", "art.png"])
    assert alb["cover"]["file"] == "cover.jpg"
    assert alb["cover"]["other_images"] == ["art.png"]
    assert alb["issues"] == []


def test_duplicate_track_detection(tmp_path):
    infos = {
        "03 Chasing Pavements.mp3": _info("Chasing Pavements", 3, 12, dur=240.0),
        "13 Chasing Pavements.mp3": _info("Chasing Pavements", 13, 12, dur=240.0),
        "14 Chasing Pavements.mp3": _info("Chasing Pavements", 14, 12, dur=219.0),  # different take
    }
    alb = _assemble(tmp_path, "Adele/19", infos)
    dupes = [i for i in alb["issues"] if i.startswith("dupe:")]
    assert len(dupes) == 1
    assert "03 Chasing Pavements.mp3 == 13 Chasing Pavements.mp3" in dupes[0]


def test_broken_album_flags_everything(tmp_path):
    infos = {
        "03 Chasing Pavements.mp3": _info("Chasing Pavements", 3, 12, genres=("Pop",), dur=240.0),
        "13 Chasing Pavements.mp3": _info("Chasing Pavements", 13, 12, genres=("Soul",), dur=240.0),
        "old name.mp3": _info("Daydreamer", 1, 12, year=None, dur=250.0),
        "song.flac": _info("Cold Shoulder", 4, 12, ext=".flac", dur=256.0),
    }
    alb = _assemble(tmp_path, "Adele/19", infos, images=["folder.png"], others=["notes.txt"])
    issues = alb["issues"]
    joined = " | ".join(issues)
    assert "layout: 2/4 files off-layout" in joined
    assert "tag: mixed genre: Pop / Soul" in joined
    assert "tag: year missing on 1/4 tracks" in joined
    assert "format: 1 flac -> convert pending" in joined
    assert "dupe: 'Chasing Pavements'" in joined
    assert "folder.png" in joined and "no cover.jpg" in joined
    assert alb["other_files"] == ["notes.txt"]
    assert alb["tracks"][0]["file"] == "old name.mp3"  # sorted by track no. (track 1 first)
    assert alb["cover"]["other_images"] == ["folder.png"]


def test_wrong_folder_name_flagged(tmp_path):
    infos = {"01 Daydreamer.mp3": _info("Daydreamer", 1, 12, artist="Adele", album="19")}
    # files filed under a folder that does not match artist/album tags
    alb = _assemble(tmp_path, "Unknown Artist/Unknown Album", infos)
    assert any("folder rename pending" in i for i in alb["issues"]), alb["issues"]
    assert any("Unknown Album" in i for i in alb["issues"])


def test_loose_files_at_root(tmp_path):
    infos = {"01 Daydreamer.mp3": _info("Daydreamer", 1, None)}
    alb = _assemble(tmp_path, "", infos)
    assert any("path depth (root)" in i for i in alb["issues"])


def test_embedded_cover_counted(tmp_path):
    infos = {"01 Daydreamer.mp3": _info("Daydreamer", 1, 12, embedded=True)}
    alb = _assemble(tmp_path, "Adele/19", infos)
    joined = " | ".join(alb["issues"])
    assert "art embedded in 1 track(s)" in joined
    assert alb["cover"]["embedded_in_count"] == 1


def test_mixed_field_values_listed(tmp_path):
    infos = {
        "01 a.mp3": _info("a", 1, 12, genres=("Pop", "Soul")),
        "02 b.mp3": _info("b", 2, 12, genres=("Jazz",)),
    }
    alb = _assemble(tmp_path, "Adele/19", infos)
    genre = alb["genre"]
    assert genre["mixed"] is True
    assert genre["current"] == ["Pop", "Soul", "Jazz"]