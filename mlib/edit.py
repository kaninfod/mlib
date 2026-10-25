"""edit — the P1 editing surface.

A ~60-line stdlib bridge (no frameworks): serves the bundled single-file
editor over http://127.0.0.1:<port>/ and exposes exactly two plan endpoints:

    GET /plan   -> the plan JSON
    PUT /plan   -> save it back (atomic write + <name>.bak rotation)

The plan path is fixed at server start (the bridge only ever touches that one
file). Local-only: binds to 127.0.0.1. All editor logic lives in the static
editor.html; the bridge only shuffles JSON bytes.
"""
from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import model
from .model import PlanError

HOST = "127.0.0.1"


class PlanBridge(BaseHTTPRequestHandler):
    server_version = "mlib-bridge/0.1"

    # these two are injected by serve()
    plan_path: Path = None
    editor_html: bytes = b""

    # ------------------------------------------------------------- helpers

    def _json_headers(self, code: int, length: int) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def log_message(self, fmt, *args):  # default logging is noise in the tool UX
        pass

    # ------------------------------------------------------------- handlers

    def do_GET(self) -> None:
        route = self.path.split("?", 1)[0]
        if route in ("/", "/editor.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(self.editor_html)))
            self.end_headers()
            self.wfile.write(self.editor_html)
        elif route == "/plan":
            try:
                body = json.dumps(model.load_plan(self.plan_path),
                                  ensure_ascii=False).encode("utf-8")
            except PlanError as e:
                payload = json.dumps({"error": str(e)}).encode("utf-8")
                self._json_headers(500, len(payload))
                self.wfile.write(payload)
                return
            self._json_headers(200, len(body))
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_PUT(self) -> None:
        route = self.path.split("?", 1)[0]
        if route != "/plan":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            model._check(data, self.plan_path)
        except (PlanError, ValueError) as e:
            payload = json.dumps({"error": str(e)}).encode("utf-8")
            self._json_headers(400, len(payload))
            self.wfile.write(payload)
            return
        try:
            model.save_plan(self.plan_path, data, backup=True)
        except OSError as e:
            payload = json.dumps({"error": str(e)}).encode("utf-8")
            self._json_headers(500, len(payload))
            self.wfile.write(payload)
            return
        self._json_headers(204, 0)


def serve(plan_path: Path, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Start the bridge; returns (server, url). port=0 -> free port."""
    plan_path = Path(plan_path)
    editor_bytes = _editor_html()
    server = ThreadingHTTPServer((HOST, port), PlanBridge)
    server.daemon_threads = True
    PlanBridge.plan_path = plan_path.resolve()
    PlanBridge.editor_html = editor_bytes
    url = f"http://{HOST}:{server.server_port}/"
    return server, url


def _editor_html() -> bytes:
    from importlib import resources

    try:
        return resources.files("mlib.assets").joinpath("editor.html") \
            .read_bytes()
    except (ModuleNotFoundError, FileNotFoundError) as e:
        raise RuntimeError(f"editor asset missing from package: {e}") from e


def run_until_interrupt(plan_path: Path, port: int = 0, open_browser: bool = True) -> None:
    server, url = serve(plan_path, port=port)
    print(f"Editing  {plan_path}")
    print(f"Browser  {url}   (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    print("Bridge stopped; unsaved browser state is not persisted (autosave kept it mostly current).")