import json

from mlib import validate
from mlib.model import new_field


def _plan():
    ok = {"path": "Adele/19", "tracks": [{"file": "01 Daydreamer.mp3"}],
          "issues": [], "genre": new_field("Pop")}
    broken = {"path": "The Doors/Unknown Album", "tracks": [{"file": "Track 1.mp3"},
                                                            {"file": "song.flac"}],
              "issues": ["tag: year missing on all 2 tracks",
                         "format: 1 flac -> convert pending",
                         "dupe: 'x' — 01 x.mp3 == 02 x.mp3"],
              "genre": new_field("Rock")}
    return {"_meta": {"tool": "mlib", "v": 2},
            "albums": [ok, broken], "_summary": {}}


def test_report_counts_and_groups():
    r = validate.validate_report(_plan())
    assert r["albums_flagged"] == 1
    assert r["albums_clean"] == 1
    assert r["issues_by_category"]["tag"] == 1
    assert r["issues_by_category"]["format"] == 1
    assert r["issues_by_category"]["dupe"] == 1
    assert r["album_list"][0]["path"] == "The Doors/Unknown Album"
    assert "albums_clean_list" not in r  # dropped without --all


def test_render_human_and_json(tmp_path):
    r = validate.validate_report(_plan())
    text = validate.render_report(r)
    assert "mlib validate — 2 albums" in text
    assert "The Doors/Unknown Album" in text
    assert "format: 1 flac -> convert pending" in text

    from mlib.cli import app  # --json path end-to-end via typer runner
    from typer.testing import CliRunner
    runner = CliRunner()
    plan_file = tmp_path / "mlib_plan.json"
    plan_file.write_text(json.dumps(_plan()))
    res = runner.invoke(app, ["--json", "validate", str(plan_file)])
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["albums_flagged"] == 1


def test_validate_accepts_root_dir(tmp_path):
    plan_file = tmp_path / "mlib_plan.json"
    plan_file.write_text(json.dumps(_plan()))
    from mlib.cli import app
    from typer.testing import CliRunner
    runner = CliRunner()
    res = runner.invoke(app, ["validate", str(tmp_path)])
    assert res.exit_code == 0, res.output
    assert "2 albums" in res.output