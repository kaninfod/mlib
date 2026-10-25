"""edit bridge tests — real HTTP against a server on an ephemeral port."""
import json
import threading
import urllib.error
import urllib.request
from http.client import HTTPConnection

import pytest

from mlib import edit as edit_mod
from mlib.model import PLAN_NAME, load_plan, save_plan


@pytest.fixture
def bridge(tmp_path):
    plan = {"_meta": {"tool": "mlib", "v": 2, "root": str(tmp_path)},
            "albums": [{"path": "A/B", "tracks": []}], "_summary": {}}
    plan_file = tmp_path / PLAN_NAME
    save_plan(plan_file, plan, backup=False)
    server, url = edit_mod.serve(plan_file, port=0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    conn = HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    yield plan_file, conn
    conn.close()
    server.shutdown()
    server.server_close()


def _get(conn, path):
    conn.request("GET", path)
    r = conn.getresponse()
    return r, r.read()


def test_editor_and_plan_served(bridge):
    plan_file, conn = bridge
    r, body = _get(conn, "/")
    assert r.status == 200 and b"mlib plan editor" in body

    r, body = _get(conn, "/plan")
    assert r.status == 200
    assert json.loads(body)["_meta"]["tool"] == "mlib"

    r, _ = _get(conn, "/nope")
    assert r.status == 404


def test_put_rounds_trip_and_baks(bridge):
    plan_file, conn = bridge
    data = load_plan(plan_file)
    data["albums"][0]["genre"] = {"current": "Pop", "new": "Rock"}
    body = json.dumps(data).encode()
    conn.request("PUT", "/plan", body=body,
                 headers={"Content-Type": "application/json"})
    r = conn.getresponse()
    assert r.status == 204

    on_disk = load_plan(plan_file)
    assert on_disk["albums"][0]["genre"]["new"] == "Rock"
    assert (plan_file.parent / (PLAN_NAME + ".bak")).exists()  # pre-save version kept

    r, body = _get(conn, "/plan")
    assert json.loads(body)["albums"][0]["genre"]["new"] == "Rock"


def test_put_rejects_non_plan_json(bridge):
    _, conn = bridge
    conn.request("PUT", "/plan", body=b'{"hello": 1}',
                 headers={"Content-Type": "application/json"})
    r = conn.getresponse()
    assert r.status == 400


def test_put_rejects_garbage(bridge):
    _, conn = bridge
    conn.request("PUT", "/plan", body=b'not json',
                 headers={"Content-Type": "application/json"})
    r = conn.getresponse()
    assert r.status == 400