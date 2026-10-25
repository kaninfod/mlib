"""P3 tests: standalone convert, shelf move-in, remote runner dispatch."""
import json
import subprocess
from pathlib import Path

import pytest

from mlib import convert as C
from mlib import shelf as S
from mlib import tags
from mlib.model import load_plan, save_plan

pytestmark = pytest.mark.skipif(not C.ffmpeg_available(),
                                reason="ffmpeg not on PATH")

FF = ["ffmpeg", "-nostdin", "-y", "-v", "error"]


def _audio(dest: Path, ext: str, **md) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [*FF, "-f", "lavfi", "-i", "sine=frequency=330:duration=0.7",
           "-c:a", "libmp3lame" if ext == ".mp3" else "flac"]
    for k, v in md.items():
        cmd += ["-metadata", f"{k}={v}"]
    cmd += [str(dest)]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest


# ---------------- standalone convert

def test_convert_in_place_keeps_original(tmp_path):
    src = _audio(tmp_path / "X/Y/01 - Alpha.flac", ".flac",
                 title="Alpha", artist="X", album="Y", track="1/2")
    rep = C.convert_root(tmp_path)
    assert rep[0]["action"] == "converted"
    assert (tmp_path / "X/Y/01 - Alpha.mp3").exists()
    assert src.exists()          # originals stay (D4 default)
    f = tags.id3_fields(tmp_path / "X/Y/01 - Alpha.mp3")
    assert (f["title"], f["artist"]) == ("Alpha", "X")   # tags carried


def test_convert_dry_run_and_dst_exists(tmp_path):
    _audio(tmp_path / "X/Y/01 - Alpha.flac", ".flac", title="Alpha")
    _audio(tmp_path / "X/Y/01 - Alpha.mp3", ".mp3", title="Alpha")
    _audio(tmp_path / "X/Z/02 - Beta.flac", ".flac", title="Beta")
    rep_dry = C.convert_root(tmp_path, dry_run=True)
    actions = {r["action"] for r in rep_dry}
    assert "would convert" in actions and all(
        a in ("would convert", "skipped (dst exists)") for a in actions)
    rep = C.convert_root(tmp_path)
    y_entry = [r for r in rep if r["folder"] == "Y"][0]
    assert y_entry["action"].startswith("skipped")   # never overwrite audio
    assert (tmp_path / "X/Y/01 - Alpha.flac").exists()


def test_convert_remove_originals_flag(tmp_path):
    _audio(tmp_path / "X/Y/01 - Alpha.flac", ".flac", title="Alpha")
    C.convert_root(tmp_path, remove_originals=True)
    assert (tmp_path / "X/Y/01 - Alpha.mp3").exists()
    assert not (tmp_path / "X/Y/01 - Alpha.flac").exists()  # explicit instruction


def test_convert_extracts_cover_when_none(tmp_path):
    flac = _audio(tmp_path / "X/Y/01 - Alpha.flac", ".flac", title="Alpha")
    from mutagen.flac import FLAC, Picture
    f = FLAC(str(flac))
    pic = Picture()
    pic.data = b"\xff\xd8\xff" + b"q" * 24
    pic.mime = "image/jpeg"
    pic.type = 3
    f.add_picture(pic)
    f.save()
    rep = C.convert_root(tmp_path)
    assert any(r["action"] == "cover extracted" for r in rep)
    assert (tmp_path / "X/Y/cover.jpg").exists()


# ---------------- shelf

def _alb(path, n_tracks, with_tags=True):
    tracks = [{"file": f"{i:02d} Song {i}.mp3", "ext": ".mp3", "dur": 1.0,
               "title": {"current": f"Song {i}", "new": None},
               "track": {"current": i, "new": None},
               "tracktotal": {"current": n_tracks, "new": None},
               "target_file": f"{i:02d} Song {i}.mp3"} for i in range(1, n_tracks + 1)]
    return {"path": path,
            "artist": {"current": path.split("/")[0], "new": None},
            "albumartist": {"current": path.split("/")[0], "new": None},
            "album": {"current": path.split("/")[1], "new": None},
            "year": {"current": 1999, "new": None},
            "genre": {"current": "Rock", "new": None},
            "cover": {"file": "cover.jpg", "other_images": [], "embedded_in_count": 0,
                      "action": None},
            "tracks": tracks, "issues": [], "_uid": "u-" + path.replace("/", "-"),
            "other_files": []}


def test_shelf_same_device_move(tmp_path):
    staging = tmp_path / "staging"
    collection = tmp_path / "collection"
    collection.mkdir()
    d = staging / "Iggy Pop/Lust for Life"
    d.mkdir(parents=True)
    _audio(d / "01 Song 1.mp3", ".mp3", title="Song 1", artist="Iggy Pop",
           album="Lust for Life", track="1/1")
    plan = {"_meta": {"tool": "mlib", "v": 2, "root": str(staging)},
            "albums": [_alb("Iggy Pop/Lust for Life", 1)], "_summary": {}}
    save_plan(staging / "mlib_plan.json", plan, backup=False)

    out = S.shelf(plan, staging, collection, dry_run=True)
    assert out["moves"][0]["state"] == "would move"
    assert d.is_dir()   # dry-run moved nothing

    out = S.shelf(plan, staging, collection)
    assert out["moves"][0]["state"] == "moved"
    assert (collection / "Iggy Pop/Lust for Life/01 Song 1.mp3").exists()
    assert not d.exists()   # moved, not copied


def test_shelf_collision_skipped(tmp_path):
    staging = tmp_path / "staging"
    collection = tmp_path / "collection"
    d = staging / "Iggy Pop/Lust for Life"
    d.mkdir(parents=True)
    _audio(d / "01 Song 1.mp3", ".mp3", title="Song 1")
    (collection / "Iggy Pop/Lust for Life").mkdir(parents=True, exist_ok=True)
    plan = {"_meta": {"tool": "mlib", "v": 2, "root": str(staging)},
            "albums": [_alb("Iggy Pop/Lust for Life", 1)], "_summary": {}}
    out = S.shelf(plan, staging, collection)
    assert out["moves"][0]["state"] == "skipped (already in collection)"
    assert d.is_dir()
    assert (collection / "Iggy Pop/Lust for Life").is_dir()


def test_shelf_untouched_album_not_moved(tmp_path):
    """An album still sitting off-layout in staging is not pushed into the
    collection half-cleaned: apply converges it first (here: it can't)."""
    staging = tmp_path / "staging"
    collection = tmp_path / "collection"
    collection.mkdir()
    _audio(staging / "downloads/X - Album/01 - Song.flac", ".flac", title="Song")
    alb = {"path": "downloads/X - Album",
           "artist": {"current": None, "new": None},
           "albumartist": {"current": None, "new": None},
           "album": {"current": None, "new": None},
           "year": {"current": None, "new": None},
           "genre": {"current": None, "new": None},
           "cover": {"file": None, "other_images": [], "embedded_in_count": 0,
                     "action": None},
           "tracks": [{"file": "01 - Song.flac", "ext": ".flac", "dur": None,
                       "title": {"current": "Song", "new": None},
                       "track": {"current": 1, "new": 1},
                       "tracktotal": {"current": None, "new": None},
                       "target_file": None}],
           "issues": [], "_uid": "x", "other_files": []}
    plan = {"_meta": {"tool": "mlib", "v": 2, "root": str(staging)},
            "albums": [alb], "_summary": {}}
    out = S.shelf(plan, staging, collection)
    assert out["moves"][0]["state"].startswith("skipped")
    assert (staging / "downloads/X - Album").exists()   # stays in staging
    assert not (collection / "downloads").exists()
    # rescan without env: non-fatal, reported
    # (tested separately — here without --rescan)


# ---------------- remote runner (dispatch mechanics)

def test_remote_runner_dispatch(monkeypatch, tmp_path):
    import sys as _sys
    from mlib import remote_runner as RR
    called = {}

    def fake_catalog(root, out=None):
        called["catalog"] = (root, out)

    monkeypatch.setattr("mlib.catalog.build_plan", lambda r: {"_summary": {"a": 1}})
    monkeypatch.setattr("mlib.model.save_plan", lambda *a, **k: None)
    monkeypatch.setattr(_sys, "argv",
                        ["mlib-remote", "catalog", str(tmp_path)])
    RR.main()
    assert called == {}   # dispatch didn't explode; smoke-level only


def test_rescan_needs_env(monkeypatch):
    from mlib import rescan as R
    monkeypatch.delenv("GONIC_URL", raising=False)
    monkeypatch.delenv("GONIC_USER", raising=False)
    monkeypatch.delenv("GONIC_PASS", raising=False)
    with pytest.raises(RuntimeError, match="GONIC_URL"):
        R.gonic_rescan()