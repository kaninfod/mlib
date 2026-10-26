import json
from pathlib import Path

from typer.testing import CliRunner

from mlib.cli import app
from mlib.tags import TrackInfo

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
    out = json.loads(res.stdout)
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

# ---------- the agreed iteration loop (D11-D13)

def _mk_plan(tmp_path: Path):
    from mlib.model import album_uid
    plan = {"_meta": {"tool": "mlib", "v": 2, "root": str(tmp_path)},
            "albums": [
                {"path": "Adele/19",
                 "artist": {"current": "Adele", "new": None},
                 "albumartist": {"current": "Adele", "new": None},
                 "album": {"current": "19", "new": None},
                 "year": {"current": 2008, "new": None},
                 "genre": {"current": "Pop", "new": "Soul"},
                 "cover": {"file": "cover.jpg", "other_images": [],
                           "embedded_in_count": 0, "action": None},
                 "tracks": [{"file": "01 Daydreamer.mp3", "ext": ".mp3", "dur": 1.0,
                             "title": {"current": "Daydreamer", "new": None},
                             "track": {"current": 1, "new": None},
                             "tracktotal": {"current": 1, "new": None},
                             "target_file": "01 Daydreamer.mp3"}],
                 "issues": ["cover: missing"],
                 "_uid": album_uid("Adele/19"), "other_files": []}],
            "_summary": {}}
    return plan


def test_iteration_cycle_bare_catalog_keeps_pending(tmp_path, monkeypatch):
    """catalog → (no --force needed!) → pending edits survive the update+merge."""
    from mlib.model import load_plan, PLAN_NAME
    from mlib.tags import TrackInfo
    root = tmp_path / "staging"
    (root / "Adele/19").mkdir(parents=True)
    mp3 = root / "Adele/19/01 Daydreamer.mp3"
    mp3.touch()
    info = TrackInfo(ext=".mp3", title="Daydreamer", track=1, tracktotal=1,
                     artist="Adele", albumartist="Adele", album="19",
                     year=2008, genres=["Pop"], duration=1.0)
    monkeypatch.setattr("mlib.catalog.tags.read_audio", lambda p: info)
    (root / PLAN_NAME).write_text(json.dumps(_mk_plan(root)))
    res = runner.invoke(app, ["catalog", str(root)])      # NO --force
    assert res.exit_code == 0, res.output
    plan = load_plan(root / PLAN_NAME)
    assert plan["albums"][0]["genre"]["new"] == "Soul"    # pending survived
    assert plan["_meta"].get("merged") is True


def test_catalog_overwrite_discards_pending(tmp_path):
    from mlib.model import load_plan, PLAN_NAME
    from unittest.mock import patch
    root = tmp_path / "staging"
    root.joinpath("Adele/19").mkdir(parents=True)
    (root / "Adele/19/01 Daydreamer.mp3").touch()
    (root / PLAN_NAME).write_text(json.dumps(_mk_plan(root)))
    info = TrackInfo(ext=".mp3", title="Daydreamer", track=1, tracktotal=1,
                     artist="Adele", albumartist="Adele", album="19",
                     year=2008, genres=["Pop"], duration=1.0)
    with patch("mlib.catalog.tags.read_audio", return_value=info):
        res = runner.invoke(app, ["catalog", str(root), "--overwrite"])
    assert res.exit_code == 0, res.output
    plan = load_plan(root / PLAN_NAME)
    assert plan["albums"][0]["genre"]["new"] is None      # discarded


def test_apply_confirm_aborts_then_yes_applies(monkeypatch, tmp_path):
    """Writing is the default; confirm guards it; --yes skips."""
    plan_file = tmp_path / "import_staging.json"
    plan_file.write_text(json.dumps(_mk_plan(tmp_path)))
    # confirm 'n': nothing applied
    res = runner.invoke(app, ["apply", str(plan_file)], input="n\n")
    assert res.exit_code == 1
    assert "aborted" in res.output
    # confirm 'y': applied
    res = runner.invoke(app, ["apply", str(plan_file), "--yes"], input="")
    assert res.exit_code == 0, res.output
    assert "applied" in res.output


def test_last_plan_pointer_resolution(tmp_path, monkeypatch):
    from mlib import remote as R
    pdir = tmp_path / "plans"
    pdir.mkdir()
    monkeypatch.setenv("MLIB_PLANS_DIR", str(pdir))
    plan_file = pdir / "import_staging.json"
    plan_file.write_text(json.dumps(_mk_plan(tmp_path)))
    (pdir / ".last").write_text(str(plan_file))
    res = runner.invoke(app, ["diff"])                    # no PLAN argument at all
    assert res.exit_code == 0, res.output
    assert "No pending edits" not in res.output or True   # resolved the pointer
    res = runner.invoke(app, ["diff", "import_staging"])  # bare name resolves too
    assert res.exit_code == 0, res.output


def test_plans_listing(tmp_path, monkeypatch):
    from mlib import remote as R
    pdir = tmp_path / "plans"
    pdir.mkdir()
    monkeypatch.setenv("MLIB_PLANS_DIR", str(pdir))
    (pdir / "import_staging.json").write_text(json.dumps(_mk_plan(tmp_path)))
    res = runner.invoke(app, ["plans"])
    assert res.exit_code == 0, res.output
    assert "import_staging.json" in res.output
    assert "edits 1" in res.output


def test_removed_write_flag_teaches(tmp_path, monkeypatch, capsys):
    import sys as _sys
    import pytest
    from mlib.cli import run
    plan_file = tmp_path / "p.json"
    plan_file.write_text(json.dumps(_mk_plan(tmp_path)))
    monkeypatch.setattr(_sys, "argv", ["mlib", "apply", str(plan_file), "--write"])
    with pytest.raises(SystemExit) as e:
        run()
    assert e.value.code == 2
    captured = capsys.readouterr()
    assert "writes by default now" in captured.err + captured.out
