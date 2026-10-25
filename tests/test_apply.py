"""apply end-to-end tests — real tiny mp3/flac files via ffmpeg (skipped when
ffmpeg is absent); exercises convert → tags → cover → renames → folder → junk
→ verify → audit exactly as production runs."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from mlib import apply as A
from mlib import convert, tags
from mlib.model import load_plan, save_plan

pytestmark = pytest.mark.skipif(not convert.ffmpeg_available(),
                                reason="ffmpeg not on PATH")


FF = ["ffmpeg", "-nostdin", "-y", "-v", "error"]


def _audio(dest: Path, ext: str, **md) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [*FF, "-f", "lavfi", "-i", "sine=frequency=330:duration=0.7"]
    cmd += ["-c:a", "libmp3lame" if ext == ".mp3" else "flac"]
    for k, v in md.items():
        cmd += ["-metadata", f"{k}={v}"]
    cmd += [str(dest)]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest


def _plan(root: Path, albums: list[dict]) -> dict:
    return {"_meta": {"tool": "mlib", "v": 2, "root": str(root)},
            "albums": albums, "_summary": {}}


def _trk(file, title, track, total=None, ext=".mp3"):
    return {"file": file, "ext": ext, "dur": None,
            "title": {"current": title, "new": None},
            "track": {"current": track, "new": None},
            "tracktotal": {"current": total, "new": None},
            "target_file": None}


def _alb(path, artist, album, year, genre, tracks, cover=None, uid="u1"):
    return {"path": path,
            "artist": {"current": artist, "new": None},
            "albumartist": {"current": artist, "new": None},
            "album": {"current": album, "new": None},
            "year": {"current": year, "new": None},
            "genre": {"current": genre, "new": None},
            "cover": cover or {"file": None, "other_images": [],
                               "embedded_in_count": 0, "action": None},
            "tracks": tracks, "issues": [], "_uid": uid, "other_files": []}


# 1) the Billie/Iggy shape: flac inbox album → materialize clean tree, husk left
def test_convert_materialize_and_husk(tmp_path):
    inbox = "downloads/1977 - Iggy Pop - Lust for Life"
    for i, (t, tr) in enumerate([("Lust for Life", 1), ("Sixteen", 2)], start=1):
        _audio(tmp_path / inbox / f"0{i} - {t}.flac", ".flac",
               title=t, artist="Iggy Pop", album="Lust for Life",
               date=1977, genre="Rock", track=f"{tr}/2")
    plan = _plan(tmp_path, [_alb(inbox, "Iggy Pop", "Lust for Life", 1977,
                                 "Rock",
                                 [_trk("01 - Lust for Life.flac", "Lust for Life", 1, 2, ".flac"),
                                  _trk("02 - Sixteen.flac", "Sixteen", 2, 2, ".flac")],
                                 uid="iggy")])
    save_plan(tmp_path / "mlib_plan.json", plan, backup=False)

    results, changed = A.run_apply(plan, tmp_path, write=True)
    assert changed and results[0]["result"] == "applied"
    a = results[0]["applied"]
    assert a["converted"] == 2 and a["verified"] is True
    assert a["folder_to"] == "Iggy Pop/Lust for Life"
    # clean tree: fresh folder with properly named, tagged mp3s
    new_dir = tmp_path / "Iggy Pop/Lust for Life"
    assert sorted(p.name for p in new_dir.iterdir()) == \
        ["01 Lust for Life.mp3", "02 Sixteen.mp3"]
    f = tags.id3_fields(new_dir / "01 Lust for Life.mp3")
    assert (f["title"], f["artist"], f["year"], f["genre"], f["track"],
            f["tracktotal"]) == ("Lust for Life", "Iggy Pop", 1977, "Rock", 1, 2)
    # originals stay behind in the husk
    husk = tmp_path / inbox
    assert sorted(p.name for p in husk.iterdir()) == \
        ["01 - Lust for Life.flac", "02 - Sixteen.flac"]
    # plan reflects post-apply truth
    alb = plan["albums"][0]
    assert alb["path"] == "Iggy Pop/Lust for Life"
    assert alb["tracks"][0]["file"] == "01 Lust for Life.mp3"
    assert alb["tracks"][0]["ext"] == ".mp3"
    assert alb["_applied"][0]["converted"] == 2

    # idempotency: second run does nothing
    results, changed = A.run_apply(plan, tmp_path, write=True)
    assert not changed and results[0]["result"] == "nochange"


# 2) pure-mp3 folder rename (trailing-space Sgt. Pepper case) + junk + cover embed
def test_mp3_folder_rename_junk_cover(tmp_path):
    bad = "The Beatles/Sgt. Pepper's Lonely Hearts Club Band "
    d = tmp_path / bad
    _audio(d / "01 - Taxman.mp3", ".mp3", title="Taxman", artist="The Beatles",
           album="Sgt. Pepper's Lonely Hearts Club Band", date=1966,
           genre="Rock", track="1/14")
    _audio(d / "02 - Eleanor Rigby.mp3", ".mp3", title="Eleanor Rigby",
           artist="The Beatles",
           album="Sgt. Pepper's Lonely Hearts Club Band", date=1966,
           genre="Rock", track="2/14")
    (d / "._01 - Taxman.mp3").write_bytes(b"appledouble")
    (d / "Cover.jpg").write_bytes(b"\xff\xd8\xff" + b"x" * 32)  # fake jpeg

    alb = _alb(bad, "The Beatles", "Sgt. Pepper's Lonely Hearts Club Band",
               1966, "Rock",
               [_trk("01 - Taxman.mp3", "Taxman", 1, 14),
                _trk("02 - Eleanor Rigby.mp3", "Eleanor Rigby", 2, 14)],
               cover={"file": "Cover.jpg", "other_images": [],
                      "embedded_in_count": 0, "action": None}, uid="pepper")
    plan = _plan(tmp_path, [alb])
    _, changed = A.run_apply(plan, tmp_path, write=True)
    assert changed
    new_dir = tmp_path / "The Beatles/Sgt. Pepper's Lonely Hearts Club Band"
    assert new_dir.is_dir() and not d.exists()          # trailing space gone
    assert "._01 - Taxman.mp3" not in [p.name for p in new_dir.iterdir()]
    assert (new_dir / "cover.jpg").exists()             # case normalized
    f = tags.id3_fields(new_dir / "01 Taxman.mp3")
    assert (f["year"], f["track"], f["tracktotal"]) == (1966, 1, 14)
    assert plan["albums"][0]["cover"]["file"] == "cover.jpg"


# 3) tracktotal auto-fill (D7) on mp3s that lack it
def test_tracktotal_autofill(tmp_path):
    d = tmp_path / "Adele/19"
    _audio(d / "01 Daydreamer.mp3", ".mp3", title="Daydreamer",
           artist="Adele", album="19", date=2008, genre="Pop", track="1")
    _audio(d / "02 Best for Last.mp3", ".mp3", title="Best for Last",
           artist="Adele", album="19", date=2008, genre="Pop", track="2")
    alb = _alb("Adele/19", "Adele", "19", 2008, "Pop",
               [_trk("01 Daydreamer.mp3", "Daydreamer", 1, None),
                _trk("02 Best for Last.mp3", "Best for Last", 2, None)],
               cover={"file": "cover.jpg", "other_images": [],
                      "embedded_in_count": 0, "action": None}, uid="adele")
    plan = _plan(tmp_path, [alb])
    A.run_apply(plan, tmp_path, write=True)
    f = tags.id3_fields(d / "01 Daydreamer.mp3")
    assert f["tracktotal"] == 2
    assert plan["albums"][0]["tracks"][0]["tracktotal"]["current"] == 2


# 4) collision = hard error, renames aborted, nothing half-done
def test_name_collision(tmp_path):
    d = tmp_path / "X/Y"
    _audio(d / "01 Same.mp3", ".mp3", title="Same", artist="X", album="Y",
           track="1/2")
    _audio(d / "02 Same.mp3", ".mp3", title="Same", artist="X", album="Y",
           track="2/2")   # editor would fix this — here tags produce dupes
    # simulate a plan where the user set both track numbers to 3
    alb = _alb("X/Y", "X", "Y", None, None,
               [_trk("01 Same.mp3", "Same", 3, 2),
                _trk("02 Same.mp3", "Same", 3, 2)], uid="x")
    plan = _plan(tmp_path, [alb])
    results, _ = A.run_apply(plan, tmp_path, write=True)
    r = results[0]
    assert r["result"] == "nochange"
    assert any("name collision" in e for e in r["errors"])
    assert sorted(p.name for p in (tmp_path / "X/Y").iterdir()) == \
        ["01 Same.mp3", "02 Same.mp3"]   # untouched


# 5) unresolved album (mixed artist, nothing decided) → fully skipped, safe
def test_unresolved_album_skipped(tmp_path):
    d = tmp_path / "downloads/1937-1946 Sessions"
    _audio(d / "01 - He Ain't Got Rhythm.flac", ".flac", title="He Ain't Got Rhythm",
           artist="Billie Holiday and Her Orchestra", track="1/2")
    alb = {"path": "downloads/1937-1946 Sessions",
           "artist": {"current": ["Billie Holiday and Her Orchestra",
                                  "Teddy Wilson and His Orchestra"], "new": None},
           "album": {"current": "1937-1946 Sessions", "new": None},
           "year": {"current": None, "new": None},
           "genre": {"current": ["Swing", "Jazz"], "new": None},
           "albumartist": {"current": None, "new": None},
           "cover": {"file": None, "other_images": [], "embedded_in_count": 0,
                     "action": None},
           "tracks": [_trk("01 - He Ain't Got Rhythm.flac",
                           "He Ain't Got Rhythm", 1, None, ".flac")],
           "issues": [], "_uid": "bh", "other_files": []}
    plan = _plan(tmp_path, [alb])
    results, changed = A.run_apply(plan, tmp_path, write=True)
    r = results[0]
    assert r["result"] == "nochange"
    assert any("unresolved" in e for e in r["errors"])
    # nothing converted, nothing written: original flac still alone in folder
    assert [p.name for p in d.iterdir()] == ["01 - He Ain't Got Rhythm.flac"]


# 6) preview changes nothing at all
def test_preview_is_readonly(tmp_path):
    d = tmp_path / "downloads/X - Album"
    _audio(d / "01 - Song.flac", ".flac", title="Song", artist="X",
           album="Album", track="1/1")
    (d / "._junk").write_bytes(b"x")
    alb = _alb("downloads/X - Album", "X", "Album", None, None,
               [_trk("01 - Song.flac", "Song", 1, None, ".flac")], uid="x2")
    plan = _plan(tmp_path, [alb])
    save_plan(tmp_path / "mlib_plan.json", plan, backup=False)
    before = {p: p.stat().st_mtime for p in tmp_path.rglob("*") if p.is_file()}
    results, changed = A.run_apply(plan, tmp_path, write=False)
    assert results[0]["result"] == "preview" and not changed
    after = {p: p.stat().st_mtime for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after
    assert load_plan(tmp_path / "mlib_plan.json")["albums"][0]["path"] == \
        "downloads/X - Album"


# 7) cover: extract embedded art into cover.jpg (the Doxy-box case shape)
def test_cover_extract_from_flac_and_embed(tmp_path):
    # flac with embedded art (jpeg)
    d = tmp_path / "downloads/1937-1946 Sessions"
    flac = _audio(d / "01 - Song.flac", ".flac", title="Song", artist="A",
                  album="Sessions", track="1/2")
    cover_bytes = b"\xff\xd8\xff" + b"z" * 40
    from mutagen.flac import FLAC, Picture
    f = FLAC(str(flac))
    pic = Picture()
    pic.data = cover_bytes
    pic.mime = "image/jpeg"
    pic.type = 3
    f.add_picture(pic)
    f.save()
    # a second track so tracktotal auto = 2
    _audio(d / "02 - Song 2.flac", ".flac", title="Song 2", artist="A",
           album="Sessions", track="2/2")
    alb = _alb("downloads/1937-1946 Sessions", "A & B", "Sessions", None, "Jazz",
               [_trk("01 - Song.flac", "Song", 1, None, ".flac"),
                _trk("02 - Song 2.flac", "Song 2", 2, None, ".flac")], uid="bh2")
    plan = _plan(tmp_path, [alb])
    results, _ = A.run_apply(plan, tmp_path, write=True)
    assert results[0]["result"] == "applied"
    new_dir = tmp_path / "A & B/Sessions"
    assert (new_dir / "cover.jpg").read_bytes() == cover_bytes
    assert results[0]["applied"]["cover_embedded"] == 2


# 8) re-catalog merge: pending decisions survive a fresh scan (by _uid)
def test_merge_rescan_keeps_pending(tmp_path):
    old = _plan(tmp_path, [_alb("downloads/X - Album", "X", "Album", None, None,
                                [_trk("01 - Song.flac", "Song", 1, None, ".flac")],
                                uid="keepme")])
    old["albums"][0]["genre"]["new"] = "Rock"          # a pending decision
    fresh = _plan(tmp_path, [_alb("X/Album", "X", "Album", None, None,
                                  [_trk("01 Song.mp3", "Song", 1, None)],
                                  uid="keepme")])
    merged = __import__("mlib.model", fromlist=["merge_rescan"]) \
        .merge_rescan(old, fresh)
    assert merged["albums"][0]["genre"]["new"] == "Rock"
    assert merged["albums"][0]["path"] == "X/Album"


# 9) missing file in plan → flagged, rest of the album still proceeds
def test_missing_file_flagged(tmp_path):
    d = tmp_path / "A/B"
    _audio(d / "01 One.mp3", ".mp3", title="One", artist="A", album="B",
           track="1/2")
    alb = _alb("A/B", "A", "B", 1999, "Pop",
               [_trk("01 One.mp3", "One", 1, 2),
                _trk("02 Gone.mp3", "Gone", 2, 2)], uid="g")
    plan = _plan(tmp_path, [alb])
    results, _ = A.run_apply(plan, tmp_path, write=True)
    r = results[0]
    assert any("missing file" in e for e in r["errors"])
    assert (tmp_path / "A/B/01 One.mp3").exists()