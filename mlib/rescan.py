"""rescan — trigger gonic's library rescan via the Subsonic API startScan.

Config via env (music_downloader's convention):
    GONIC_URL   e.g. http://192.168.68.101:4747
    GONIC_USER  a gonic user (api-capable)
    GONIC_PASS  its password

Failure is loud and instructive; apply/shelf treat rescan as non-fatal.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request


def _settings(url: str, user: str, pw: str) -> tuple[str, str, str]:
    url = (url or os.environ.get("GONIC_URL", "")).rstrip("/")
    user = user or os.environ.get("GONIC_USER", "")
    pw = pw or os.environ.get("GONIC_PASS", "")
    missing = [n for n, v in (("GONIC_URL", url), ("GONIC_USER", user),
                              ("GONIC_PASS", pw)) if not v]
    if missing:
        raise RuntimeError(
            "gonic rescan needs env: " + ", ".join(missing)
            + " — set them (e.g. in ~/.zshrc) or call with --url/--user/--pass")
    return url, user, pw


def gonic_rescan(url: str = "", user: str = "", pw: str = "") -> str:
    url, user, pw = _settings(url, user, pw)
    q = urllib.parse.urlencode({"u": user, "p": pw, "v": "1.16.1", "c": "mlib",
                                "f": "json"})
    endpoint = f"{url}/rest/startScan?{q}" if "/rest" not in url \
        else f"{url.rstrip('/')}/startScan?{q}"
    req = urllib.request.Request(endpoint, headers={"User-Agent": "mlib/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"startScan call failed ({endpoint}): {e}")
    sub = data.get("subsonic-response", {})
    if sub.get("status") != "ok":
        raise RuntimeError(f"startScan refused: {json.dumps(sub)[:200]}")
    scan = sub.get("scanStatus", {})
    return f"gonic rescanning (scanning={scan.get('scanning')})"