"""remote — `--host` execution: run file-touching subcommands on another
machine over ssh (design §8).

The remote side needs python3.10+ and mutagen only (no typer): the mlib
package is synced to `~/mlib_src` and its stdlib `remote_runner.py` runs
there. Plan JSONs travel both ways via rsync to a local working copy
(MLIB_PLANS_DIR or ~/.mlib/plans) — the Mac edits, the NAS converges.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

RUNNER = "python3 ~/mlib_src/mlib/remote_runner.py"


def _run(cmd: list[str], stdin_text: str | None = None,
         timeout: int = 600) -> str:
    proc = subprocess.run(cmd, input=stdin_text, capture_output=True,
                          text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"ssh/rsync failed ({proc.returncode}):\n"
                           f"{proc.stderr.strip()[:600]}")
    return proc.stdout


def ssh_out(host: str, code: str, timeout: int = 600) -> str:
    # run from ~/mlib_src so plain `from mlib import ...` imports work
    return _run(["ssh", "-o", "BatchMode=yes", host,
                 "cd ~/mlib_src && python3 -"],
                stdin_text=code, timeout=timeout)


def sync_source(host: str) -> None:
    """Deploy the mlib package to ~/mlib_src (idempotent, fast)."""
    here = Path(__file__).resolve().parent
    _run(["rsync", "-a", "--exclude", "__pycache__", str(here) + os.sep,
          f"{host}:~/mlib_src/mlib/"])


def plans_dir() -> Path:
    env = os.environ.get("MLIB_PLANS_DIR")
    d = Path(env) if env else Path.home() / ".mlib" / "plans"
    d.mkdir(parents=True, exist_ok=True)
    return d


def working_copy_for(plan_path: Path) -> Path:
    """Local working-copy name for a remote plan. Default-named plans
    become '<root_dir_basename>.json' (e.g. import_staging.json) so that
    multiple remote roots don't collide; custom --out names pass through."""
    rp = Path(plan_path)
    if rp.name == "mlib_plan.json":
        stem = rp.parent.name or "plan"
        return plans_dir() / f"{stem}.json"
    return plans_dir() / rp.name


def _rsync_path(host: str, spec: str) -> str:
    return spec if host == "" else f"{host}:{spec}"


def pull_plan(host: str, remote_plan: str) -> Path:
    local = working_copy_for(remote_plan)
    _run(["rsync", "-a", _rsync_path(host, remote_plan), str(local)])
    return local


def push_plan(host: str, local_plan: Path) -> str:
    remote_tmp = "~/mlib_src/.plan_push.json"
    _run(["rsync", "-a", str(local_plan), _rsync_path(host, remote_tmp)])
    return remote_tmp


def _last_json(out: str) -> dict:
    for line in reversed(out.strip().splitlines()):
        try:
            return json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
    return {}


# ------------------------------------------------------------ --host commands

def remote_catalog(host: str, root: str, out: str | None) -> tuple[str, dict]:
    code = ("from mlib.remote_runner import main\n"
            f"import sys\n"
            f"sys.argv = ['mlib-remote', 'catalog', {root!r}"
            + (f", '--out', {out!r}" if out else "") + "]\n"
            "main()\n")
    out_text = ssh_out(host, code)
    info = _last_json(out_text)
    return info.get("plan", "~/mlib_src"), info.get("summary", {})


def remote_apply(host: str, local_plan: Path, write: bool,
                 only: set[str] | None, keep_going: bool,
                 bitrate: str) -> str:
    remote_plan = push_plan(host, local_plan)
    code = ("from mlib.remote_runner import main\nimport sys\n"
            f"sys.argv = ['mlib-remote', 'apply', {remote_plan!r}"
            + (", '--write'" if write else "")
            + ("" + "".join(f", '--album', {a!r}" for a in sorted(only or [])))
            + (", '--keep-going'" if keep_going else "")
            + f", '--bitrate', {bitrate!r}]\nmain()\n")
    return ssh_out(host, code, timeout=1800)


def remote_shelf(host: str, local_plan: Path, into: str, dry_run: bool,
                 rescan: bool, bitrate: str) -> str:
    remote_plan = push_plan(host, local_plan)
    code = ("from mlib.remote_runner import main\nimport sys\n"
            f"sys.argv = ['mlib-remote', 'shelf', {remote_plan!r}, "
            f"'--into', {into!r}"
            + (", '--dry-run'" if dry_run else "")
            + (", '--rescan'" if rescan else "")
            + f", '--bitrate', {bitrate!r}]\nmain()\n")
    return ssh_out(host, code, timeout=3600)


def remote_convert(host: str, root: str, bitrate: str,
                   remove_originals: bool, dry_run: bool = False) -> str:
    code = ("from mlib.remote_runner import main\nimport sys\n"
            f"sys.argv = ['mlib-remote', 'convert', {root!r}, "
            f"'--bitrate', {bitrate!r}"
            + (", '--remove-originals'" if remove_originals else "")
            + (", '--dry-run'" if dry_run else "") + "]\n"
            "main()\n")
    return ssh_out(host, code, timeout=3600)


def remote_doctor(host: str) -> dict:
    out = ssh_out(host, (
        "import sys, subprocess, shutil\n"
        "info = {'python': sys.version.split()[0]}\n"
        "try:\n"
        "    import mutagen; info['mutagen'] = mutagen.version_string\n"
        "except Exception as e:\n"
        "    info['mutagen'] = f'MISSING ({e})'\n"
        "info['ffmpeg'] = 'yes' if shutil.which('ffmpeg') else 'MISSING'\n"
        "print('MLIB_REMOTE_JSON ' + __import__('json').dumps(info))\n"))
    for line in out.splitlines():
        if line.startswith("MLIB_REMOTE_JSON "):
            return json.loads(line[len("MLIB_REMOTE_JSON "):])
    return {"python": "?", "mutagen": "?", "ffmpeg": "?"}