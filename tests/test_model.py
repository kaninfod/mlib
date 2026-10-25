import json

import pytest

from mlib.model import (PlanError, effective, load_plan, new_field, pending,
                        pending_changes, plan_counts, save_plan)


def test_field_dict_semantics():
    f = new_field("Pop")
    assert f == {"current": "Pop", "new": None}
    assert effective(f) == "Pop"
    assert not pending(f)
    f["new"] = "Soul"
    assert effective(f) == "Soul"
    assert pending(f)
    f["new"] = "Pop"  # no-op decision
    assert not pending(f)


def test_save_load_roundtrip(tmp_path):
    plan = {"_meta": {"tool": "mlib", "v": 2, "root": "/x", "created": None},
            "albums": [{"path": "A/B", "tracks": []}], "_summary": {}}
    target = tmp_path / "mlib_plan.json"
    save_plan(target, plan)
    assert load_plan(target)["albums"][0]["path"] == "A/B"
    assert json.loads(target.read_text())["_meta"]["tool"] == "mlib"


def test_save_rotates_bak(tmp_path):
    target = tmp_path / "mlib_plan.json"
    save_plan(target, {"_meta": {"tool": "mlib", "v": 2}, "albums": [], "_summary": {}})
    save_plan(target, {"_meta": {"tool": "mlib", "v": 2}, "albums": [], "_summary": {}},
              backup=True)
    assert (tmp_path / "mlib_plan.json.bak").exists()
    # no .tmp leftovers
    assert not list(tmp_path.glob("*.tmp*"))


def test_load_rejects_garbage(tmp_path):
    bad = tmp_path / "plan.json"
    bad.write_text("{not json")
    with pytest.raises(PlanError):
        load_plan(bad)
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"hello": 1}))
    with pytest.raises(PlanError, match="not an mlib plan"):
        load_plan(wrong)


def _album(**fields):
    base = {
        "path": "A/B",
        "artist": new_field("A"),
        "albumartist": new_field(None),
        "album": new_field("B"),
        "year": new_field(2001),
        "genre": new_field("Pop"),
        "cover": {"file": "cover.jpg", "other_images": [], "embedded_in_count": 0, "action": None},
        "tracks": [{"file": "01 x.mp3", "ext": ".mp3", "dur": 1.0,
                    "title": new_field("x"), "track": new_field(1),
                    "tracktotal": new_field(1), "target_file": "01 x.mp3"}],
        "issues": [],
        "_uid": "abc",
        "other_files": [],
    }
    for k, v in fields.items():
        if k == "tracks":
            base["tracks"] = v
        else:
            base[k] = v
    return base


def test_pending_changes_album_and_track():
    alb = _album(genre=new_field("Pop", ))
    alb["genre"]["new"] = "Soul"
    trk = _album()["tracks"][0]
    trk["title"]["new"] = "x (live)"
    alb["tracks"] = [trk]
    rows = pending_changes(alb)
    fields = {(r["field"], r.get("file")) for r in rows}
    assert ("genre", None) in fields
    assert ("title", "01 x.mp3") in fields
    alb2 = _album()
    alb2["genre"]["new"] = "Pop"  # same as current -> not pending
    assert pending_changes(alb2) == []


def test_plan_counts():
    plan = {"_meta": {"tool": "mlib", "v": 2}, "albums": [], "_summary": {}}
    a = _album()
    alb2 = _album(path="C/D", artist=new_field("C"), album=new_field("D"))
    alb2["genre"]["current"] = "Soul"
    a["genre"]["new"] = "Soul"
    plan["albums"] = [a, alb2]
    plan["albums"][0]["issues"] = ["layout: x", "tag: x"]
    counts = plan_counts(plan)
    assert counts["albums"] == 2
    assert counts["tracks"] == 2
    assert counts["genres"] == 2
    assert counts["issues"] == 2
    assert counts["edits"] == 1
    assert counts["albums_with_edits"] == 1