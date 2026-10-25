import json
from pathlib import Path

from typer.testing import CliRunner

from mlib.cli import app

runner = CliRunner()


def test_diff_shows_pending_edits(tmp_path):
    plan = {
        "_meta": {"tool": "mlib", "v": 2},
        "albums": [
            {"path": "Adele/19",
             "artist": {"current": "Adele", "new": None},
             "genre": {"current": "Pop", "new": "Soul"},
             "year": {"current": 2008, "new": None},
             "album": {"current": "19", "new": None},
             "albumartist": {"current": "Adele", "new": None},
             "cover": {"file": "cover.jpg", "action": None},
             "tracks": [{"file": "03 Chasing Pavements.mp3", "ext": ".mp3", "dur": 240.0,
                         "title": {"current": "Chasing Pavements", "new": "Chasing Pavements (live)"},
                         "track": {"current": 3, "new": None},
                         "tracktotal": {"current": 12, "new": None},
                         "target_file": "03 Chasing Pavements.mp3"}],
             "issues": [], "_uid": "x", "other_files": []},
        ],
        "_summary": None,
    }
    plan_file = tmp_path / "mlib_plan.json"
    plan_file.write_text(json.dumps(plan))

    res = runner.invoke(app, ["diff", str(plan_file)])
    assert res.exit_code == 0
    assert "2 change(s) in 1 album(s)" in res.output
    assert "genre: 'Pop' -> 'Soul'" in res.output
    assert "Chasing Pavements (live)" in res.output

    res = runner.invoke(app, ["--json", "diff", str(plan_file)])
    assert res.exit_code == 0
    out = json.loads(res.output)
    assert out["edits"] == 2
    assert out["albums"] == ["Adele/19"]


def test_diff_accepts_root(tmp_path):
    plan_file = tmp_path / "mlib_plan.json"
    plan_file.write_text(json.dumps({"_meta": {"tool": "mlib", "v": 2},
                                     "albums": [], "_summary": {}}))
    res = runner.invoke(app, ["diff", str(tmp_path)])
    assert res.exit_code == 0
    assert "No pending edits" in res.output


def test_diff_empty_plan_message(tmp_path):
    plan_file = tmp_path / "other.json"
    plan_file.write_text(json.dumps({"_meta": {"tool": "mlib", "v": 2},
                                     "albums": [], "_summary": {}}))
    res = runner.invoke(app, ["diff", str(plan_file)])
    assert res.exit_code == 0
    assert "No pending edits" in res.output


def test_exists_plan(tmp_path):
    res = runner.invoke(app, ["diff", str(tmp_path / "nope.json")])
    assert res.exit_code != 0  # typer BadParameter/exit from _resolve_plan
    res = runner.invoke(app, ["validate", str(tmp_path / "nodir" / "x.json")])
    assert res.exit_code != 0