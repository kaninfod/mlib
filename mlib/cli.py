"""cli — the mlib console app (typer).

All commands shipped: catalog / validate / diff (P0), edit (P1), apply (P2),
convert / shelf / rescan / doctor / --host (P3).
"""
from __future__ import annotations

import json as _json
import os
import re
from collections import Counter
from pathlib import Path

import typer

from . import apply as apply_mod
from . import catalog as catalog_mod
from . import convert as convert_mod
from . import edit as edit_mod
from . import model, remote as remote_mod
from . import shelf as shelf_mod
from . import validate as validate_mod
from .model import PLAN_NAME, PlanError

PLAN_ARG_HELP = ("Plan file, a bare plan name in ~/.mlib/plans/ "
                 "(e.g. 'import_staging'), or a local root folder")

app = typer.Typer(add_completion=False,
                  help="mlib — file, tag and cover your music collection to one standard.")


def _emit(ctx: typer.Context, machine, human: str) -> None:
    """Print JSON (--json) or the human text."""
    if ctx.obj and ctx.obj.get("json_out"):
        typer.echo(_json.dumps(machine, ensure_ascii=False, indent=2))
    else:
        typer.echo(human)


def _resolve_plan(target) -> Path:
    """Resolve a PLAN argument (design: plan-ish commands always speak PLAN):

    1. an existing path  — a plan JSON file, or a local ROOT folder (uses
       <root>/mlib_plan.json)
    2. a bare name       — 'import_staging' -> ~/.mlib/plans/import_staging.json
       (or its .plan variant / the mlib_plan.json working copy), including
       remote-root names in --host mode (catalog names the copy after the root)
    Never a remote path: with --host, folder paths belong to commands that
    touch files (catalog/apply/shelf/convert); plans live in the plans dir.
    """
    t = Path(target)
    candidates: list[Path] = []
    if t.exists():
        return t / PLAN_NAME if t.is_dir() else t
    pdir = remote_mod.plans_dir()
    stem = t.name[:-5] if t.name.endswith(".json") else t.name
    candidates += [
        pdir / (t.name if t.name.endswith(".json") else f"{t.name}.json"),
        pdir / f"{stem}.json",
        remote_mod.working_copy_for(f"{target}/{PLAN_NAME}"),
        remote_mod.working_copy_for(t),
    ]
    for c in candidates:
        if c.exists():
            return c
    raise typer.BadParameter(
        f"plan not found: {target} — give a plan file path, a root folder, or a "
        f"bare name present in {pdir}")


@app.callback()
def main(ctx: typer.Context,
         json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
         verbose: bool = typer.Option(False, "--verbose", "-v", help="More detail where applicable"),
         host: str = typer.Option(None, "--host", help="Run against files on a remote machine over ssh (the plan travels to/from a local working copy). Default: env MLIB_HOST")):
    ctx.obj = {"json_out": json_out, "verbose": verbose,
               "host": host or os.environ.get("MLIB_HOST") or None}


@app.command()
def catalog(ctx: typer.Context,
            root: Path = typer.Argument(..., file_okay=False,
                                        help="Folder to scan (collection, staging, any music root; with --host: remote path)"),
            host: str = typer.Option(None, "--host", help="Run against files on this machine over ssh (same as the global --host; may be given either way)"),
            out: Path = typer.Option(None, "--out", help="Plan path (default <root>/mlib_plan.json)"),
            force: bool = typer.Option(False, "--force", help="Overwrite an existing plan (previous saved as .bak); pending edits are carried over via merge"),
            open_ed: bool = typer.Option(False, "--open", help="Open the editor straight after cataloging (serves until Ctrl+C)")):
    """Scan a root and produce the edit plan (mlib_plan.json).

    Examples:
      mlib --host store catalog /mnt/store/music/import_staging --force
      mlib catalog ~/new_music                   (local folder: no --host)
    The plan lands next to the data + a local working copy in ~/.mlib/plans;
    --force regenerates (pending edits survive the merge)."""
    h = host or (ctx.obj or {}).get("host")
    if h:
        remote_mod.sync_source(h)          # keep ~/mlib_src fresh (fast, idempotent)
        plan_str, _ = remote_mod.remote_catalog(h, str(root),
                                                str(out) if out else None)
        plan = model.load_plan(remote_mod.pull_plan(h, plan_str))
        local_plan = remote_mod.working_copy_for(plan_str)
        counts = model.plan_counts(plan)
        by_cat = Counter(i.split(":", 1)[0] for alb in plan["albums"]
                         for i in alb.get("issues", []))
        cat_str = " · ".join(f"{c}: {n}" for c, n in sorted(by_cat.items()))
        human = [
            f"Catalogued on {h}: {root} -> {plan_str}",
            f"Local working copy: {local_plan}",
            f"Albums {counts['albums']} · tracks {counts['tracks']} · "
            f"genres {counts['genres']} · issues {counts['issues']}"
            + (f" ({cat_str})" if cat_str else " — clean!"),
            "",
            f"Edit:    mlib edit {local_plan}",
            f"Review:  mlib validate {local_plan}",
            f"Diff:    mlib diff {local_plan}",
        ]
        _emit(ctx, plan, "\n".join(human))
        if open_ed:
            edit_mod.run_until_interrupt(local_plan, port=0, open_browser=True)
        return
    if not Path(root).is_dir():
        raise typer.BadParameter(f"not a directory: {root}")
    plan_path = Path(out) if out else Path(root) / PLAN_NAME
    if plan_path.exists() and not force:
        raise typer.BadParameter(f"{plan_path} exists — pass --force to regenerate "
                                 f"(previous version is kept as .bak)")
    old_plan = None
    if plan_path.exists():
        try:
            old_plan = model.load_plan(plan_path)
        except PlanError:
            pass
    try:
        fresh = catalog_mod.build_plan(Path(root), previous=old_plan)
    except FileNotFoundError as e:
        raise typer.BadParameter(str(e))
    if force and plan_path.exists():
        try:
            fresh = model.merge_rescan(old_plan, fresh)   # pending edits survive
            fresh["_meta"]["merged"] = True
        except (PlanError, TypeError):
            pass
    model.save_plan(plan_path, fresh, backup=plan_path.exists())
    plan = fresh
    counts = model.plan_counts(plan)

    by_cat = Counter(i.split(":", 1)[0] for alb in plan["albums"] for i in alb.get("issues", []))
    cat_str = " · ".join(f"{c}: {n}" for c, n in sorted(by_cat.items()))

    human = [
        f"Catalogued {counts['albums']} albums, {counts['tracks']} tracks -> {plan_path}",
        f"Genres: {counts['genres']}   Issues: {counts['issues']}"
        + (f" ({cat_str})" if cat_str else " — none"),
        "",
        f"Edit:    mlib edit {plan_path}",
        f"Review:  mlib validate {plan_path}",
        f"Diff:    mlib diff {plan_path}",
    ]
    _emit(ctx, plan, "\n".join(human))
    if open_ed:
        edit_mod.run_until_interrupt(plan_path, port=0, open_browser=True)


@app.command()
def apply(ctx: typer.Context,
          plan: Path = typer.Argument(..., help=PLAN_ARG_HELP),
          write: bool = typer.Option(False, "--write", help="Execute the changes (default: preview only)"),
          album: list[str] = typer.Option(None, "--album", help="Restrict to album path(s) — repeatable"),
          keep_going: bool = typer.Option(False, "--keep-going", help="Continue on album errors instead of stopping"),
          bitrate: str = typer.Option("320k", "--bitrate", help="mp3 bitrate for conversions (default 320k CBR)"),
          host: str = typer.Option(None, "--host", help="Run against files on this machine over ssh (same as the global --host; may be given either way)")):
    """Converge files toward the plan: convert, tag, cover, rename, clean.

    Preview by default — pass --write to actually change files.

    PLAN is your edited JSON (a path, a bare name in ~/.mlib/plans, or a
    local root). The folder of files comes from the PLAN itself (_meta.root,
    set at catalog time) — with --host, that is where the work runs.

    The plan is updated to the post-apply state; run
    `mlib catalog <root> --force` afterwards for a full refresh (pending
    edits survive the merge).

    Examples:
      mlib apply import_staging --write --host store        (MLIB_HOST works too)
      mlib apply import_staging                             (preview only)
      mlib apply import_staging --write --album "Adele/19"
    """
    plan_path = _resolve_plan(plan)
    plan = model.load_plan(plan_path)
    h = host or (ctx.obj or {}).get("host")
    only = set(album) if album else None

    if h:
        try:
            out_text = remote_mod.remote_apply(h, plan_path, write=write,
                                               only=only, keep_going=keep_going,
                                               bitrate=bitrate)
        except RuntimeError as e:
            raise typer.BadParameter(f"remote apply failed: {e}")
        # bring the updated remote plan back over the local one
        plan = model.load_plan(plan_path)
        remote_root = plan.get("_meta", {}).get("root")
        remote_plan = "~/mlib_src/.plan_push.json"
        pulled = remote_mod.pull_plan(h, remote_plan)
        human = out_text.strip()
        human += f"\n\n(remote plan synced back to local working copy: {pulled})"
        _emit(ctx, results := {"host_out": human}, human)  # noqa: F841
        return

    root = Path(plan.get("_meta", {}).get("root") or plan_path.parent)
    if not root.is_dir():
        raise typer.BadParameter(f"plan root does not exist on this machine: "
                                 f"{root} — run apply where the files live "
                                 f"(or pass --host)")
    results, changed = apply_mod.run_apply(plan, root, write=write,
                                           only=only, keep_going=keep_going,
                                           bitrate=bitrate)
    if write and changed:
        model.save_plan(plan_path, plan, backup=True)
    human = apply_mod.print_results(results, write)
    if write and changed:
        human += ("\n\nPlan updated to post-apply state. A full refresh of "
                  "issues/counts: mlib catalog <root> --force")
    _emit(ctx, results, human)


@app.command()
def edit(ctx: typer.Context,
         plan: Path = typer.Argument(..., help=PLAN_ARG_HELP + " (in --host mode a remote root maps to its local working copy)"),
         port: int = typer.Option(0, "--port", help="Bridge port (default: any free port)"),
         no_open: bool = typer.Option(False, "--no-open", help="Do not launch a browser, just serve"),
         host: str = typer.Option(None, "--host", help="Resolve paths against this machine's plans (plan-file ops are always local; same as the global --host)")):
    """Edit a plan in your browser: localhost bridge + bundled static editor.

    Works in Firefox, Chromium and Safari. Edits autosave (server keeps .bak);
    Ctrl+C stops the bridge. With --host, a remote root maps to its local
    working copy (~/.mlib/plans/…) — plan-file operations are always local
    (no file work happens remotely here).

    Examples:
      mlib edit import_staging                 (bare plan name)
      mlib --host store edit /mnt/store/music/import_staging
      mlib edit ~/.mlib/plans/import_staging.json --no-open"""
    h = host or (ctx.obj or {}).get("host")
    plan_path: Path | None = None
    if h:
        # --host mode: map the remote root/file onto its local working copy
        cand = remote_mod.working_copy_for(f"{plan}/{PLAN_NAME}")
        alt = remote_mod.working_copy_for(Path(plan))
        plan_path = next((c for c in (cand, alt) if c.exists()), None)
        if plan_path is None:
            raise typer.BadParameter(
                f"no local working copy for {plan} — run "
                f"'mlib --host {h} catalog {plan}' first "
                f"(it would create {cand})")
        typer.echo(f"editing local working copy: {plan_path}")
    else:
        plan_path = _resolve_plan(plan)
    model.load_plan(plan_path)  # fail fast on corrupt plans
    edit_mod.run_until_interrupt(plan_path, port=port, open_browser=not no_open)


@app.command()
def validate(ctx: typer.Context,
             plan: Path = typer.Argument(..., help=PLAN_ARG_HELP),
             all_albums: bool = typer.Option(False, "--all", help="List clean albums too")):
    """Compliance report: what breaks the standard, per album."""
    plan = model.load_plan(_resolve_plan(plan))
    report = validate_mod.validate_report(plan, show_clean=all_albums)
    report = {k: v for k, v in report.items() if v is not None}
    _emit(ctx, report, validate_mod.render_report(report))


@app.command()
def diff(ctx: typer.Context,
         plan: Path = typer.Argument(..., help=PLAN_ARG_HELP),
         verbose: bool = typer.Option(False, "--verbose", "-v", help="Per-track edit lines")):
    """Show pending edits (new != current) before applying."""
    plan = model.load_plan(_resolve_plan(plan))
    rows = []
    albums_pending: list[str] = []
    for alb in plan.get("albums", []):
        changes = model.pending_changes(alb)
        if changes:
            albums_pending.append(alb["path"])
            rows.append({"album": alb["path"], "changes": changes})
    counts = model.plan_counts(plan)

    def fmt(v):
        return "—" if v is None else str(v)

    def render():
        lines = [f"Pending edits: {counts['edits']} change(s) "
                 f"in {counts['albums_with_edits']} album(s)"]
        for entry in rows:
            lines.append(f"\n{entry['album']}")
            for ch in entry["changes"]:
                mark = " (✦ inferred)" if ch.get("inferred") else ""
                if ch.get("file"):
                    lines.append(f"  {ch['file']}")
                    lines.append(f"    {ch['field']}: '{fmt(ch['current'])}' -> '{fmt(ch['new'])}'{mark}")
                else:
                    lines.append(f"  {ch['field']}: '{fmt(ch['current'])}' -> '{fmt(ch['new'])}'{mark}")
        return "\n".join(lines)

    _emit(ctx, {"edits": counts["edits"], "albums": albums_pending, "changes": rows},
          render() if rows else "No pending edits — the plan matches disk.")


# which subcommand owns which flag — used to teach the flag-tier rule on misuse
_FLAG_OWNERS = {
    "--remove-originals": "convert", "--bitrate": "apply/shelf/convert",
    "--write": "apply", "--album": "apply / shelf", "--keep-going": "apply",
    "--into": "shelf", "--dry-run": "shelf / convert", "--rescan": "shelf",
    "--force": "catalog", "--out": "catalog", "--open": "catalog",
    "--port": "edit", "--no-open": "edit", "--all": "validate",
    "--url": "rescan", "--user": "rescan", "--pass": "rescan",
}


def run() -> None:
    try:
        # non-standalone mode so we can turn usage errors into teaching moments
        app(standalone_mode=False)
    except PlanError as e:
        typer.echo(f"error: {e}", err=True)
        raise SystemExit(2)
    except Exception as e:  # noqa: BLE001 — click usage errors included here
        msg = getattr(e, "message", None) or str(e)
        m = re.search(r"No such option: (--[a-zA-Z-]+)", msg)
        owner = _FLAG_OWNERS.get(m.group(1)) if m else None
        typer.echo(msg, err=True)
        if owner:
            typer.echo(
                f"\n'{m.group(1)}' belongs to the {owner} command — the shape is "
                f"'mlib [global flags] {owner} … {m.group(1)}' "
                f"(global flags (--host, --json, -v) are the ones allowed "
                f"before the command):", err=True)
            typer.echo(f"    mlib [--host store] {owner} … {m.group(1)}",
                       err=True)
            typer.echo("Try 'mlib <command> --help' for that command's flags.",
                       err=True)
        else:
            typer.echo("Try 'mlib --help' for help.", err=True)
        raise SystemExit(2)


if __name__ == "__main__":
    run()

@app.command()
def convert(ctx: typer.Context,
            root: Path = typer.Argument(..., help="Folder to convert in place (with --host: remote path)"),
            bitrate: str = typer.Option("320k", "--bitrate", help="mp3 bitrate (default 320k CBR)"),
            dry_run: bool = typer.Option(False, "--dry-run", help="List what would convert"),
            remove_originals: bool = typer.Option(False, "--remove-originals", help="DELETE each source after verified conversion (off by default — originals always stay)"),
            host: str = typer.Option(None, "--host", help="Run against files on this machine over ssh (same as the global --host; may be given either way)")):
    """Standalone lossless→mp3 conversion, no plan needed.

    flac/opus/ogg/webm → mp3 320 CBR in the same folder, tags carried, cover.jpg
    materialized when a folder has none but its sources carry art. Run the
    catalog loop afterwards to validate/fix tags.

    Examples:
      mlib --host store convert /mnt/store/music/import_staging --dry-run
      mlib --host store convert "<album folder>" --remove-originals
      mlib convert ~/ripped_flac                    (local: no --host)"""
    h = host or (ctx.obj or {}).get("host")
    if h:
        try:
            out_text = remote_mod.remote_convert(h, str(root), bitrate,
                                                 remove_originals,
                                                 dry_run=dry_run)
        except RuntimeError as e:
            raise typer.BadParameter(f"remote convert failed: {e}")
        _emit(ctx, {"host_out": out_text}, out_text.strip() + f"\n\n(on {h})")
        return
    if not Path(root).is_dir():
        raise typer.BadParameter(f"not a directory: {root}")
    report = convert_mod.convert_root(Path(root), bitrate=bitrate,
                                      dry_run=dry_run,
                                      remove_originals=remove_originals)
    n_conv = sum(1 for r in report if r.get("action") == "converted")
    n_skip = sum(1 for r in report if (r.get("action") or "").startswith("skipped"))
    lines = [f"convert {root}" + (" (dry-run)" if dry_run else "") +
             f": {n_conv} converted · {n_skip} skipped" +
             (f" · originals REMOVED" if remove_originals and not dry_run else "")]
    for r in report:
        lines.append(f"  {r['folder']}: {r['src']} -> {r['dst']}  {r['action']}")
    _emit(ctx, report, "\n".join(lines) if report else "nothing to convert")


@app.command()
def shelf(ctx: typer.Context,
          plan: Path = typer.Argument(..., help=PLAN_ARG_HELP),
          into: Path = typer.Option(..., "--into", help="Collection root to move converged albums INTO (with --host: remote path; default env MLIB_HOST too)"),
          dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen"),
          album: list[str] = typer.Option(None, "--album", help="Restrict to album path(s) — repeatable"),
          rescan: bool = typer.Option(False, "--rescan", help="Trigger gonic rescan after moving (env GONIC_URL/USER/PASS)"),
          bitrate: str = typer.Option("320k", "--bitrate", help="mp3 bitrate for conversions during the apply stage (default 320k CBR)"),
          host: str = typer.Option(None, "--host", help="Run against files on this machine over ssh (same as the global --host; may be given either way)")):
    """Apply pending work, then move the plan's album folders into a collection root.

    Examples:
      mlib --host store shelf import_staging --dry-run --into /mnt/store/music/collection
      mlib --host store shelf import_staging --into /mnt/store/music/collection --rescan
    (PLAN by bare name resolves in ~/.mlib/plans; --host is the machine the
    albums currently live on — the same tier rules as everywhere else.)"""
    plan_path = _resolve_plan(plan)
    plan = model.load_plan(plan_path)
    h = host or (ctx.obj or {}).get("host")
    only = set(album) if album else None
    if h:
        try:
            out_text = remote_mod.remote_shelf(h, plan_path, str(into),
                                               dry_run=dry_run, rescan=rescan,
                                               bitrate=bitrate)
        except RuntimeError as e:
            raise typer.BadParameter(f"remote shelf failed: {e}")
        _emit(ctx, {"host_out": out_text}, out_text.strip())
        return
    from_root = Path(plan.get("_meta", {}).get("root") or plan_path.parent)
    try:
        out = shelf_mod.shelf(plan, from_root, Path(into), dry_run=dry_run,
                              only=only, rescan=rescan, bitrate=bitrate)
    except RuntimeError as e:
        raise typer.BadParameter(f"shelf failed: {e}")
    _emit(ctx, out, shelf_mod.render_shelf(out))


@app.command()
def rescan(ctx: typer.Context,
           url: str = typer.Option(None, "--url", help="gonic base URL (default env GONIC_URL)"),
           user: str = typer.Option(None, "--user", help="gonic user (default env GONIC_USER)"),
           password: str = typer.Option(None, "--pass", help="password (default env GONIC_PASS)")):
    """Trigger gonic's library rescan via the Subsonic startScan API.

    Reads env GONIC_URL / GONIC_USER / GONIC_PASS by default."""
    from . import rescan as rescan_mod
    result = rescan_mod.gonic_rescan(url=url, user=user, pw=password)
    _emit(ctx, {"rescan": result}, result)


@app.command()
def doctor(ctx: typer.Context,
           host: str = typer.Option(None, "--host", help="Also check a remote machine"),
           sync: bool = typer.Option(False, "--sync", help="Deploy/refresh the mlib package on the remote")):
    """Environment check: python, mutagen, typer, ffmpeg, ssh, editor asset."""
    import sys as _sys
    lines: list[str] = []
    issues: list[str] = []

    def check(name: str, ok: bool, detail: str) -> None:
        lines.append(f"  {'✅' if ok else '⚠'} {name}: {detail}")
        if not ok:
            issues.append(name)

    v = getattr(_sys, "version_info", None)
    check("python", v >= (3, 10), f"{_sys.version.split()[0]}")
    try:
        import mutagen
        check("mutagen", True,
              getattr(mutagen, "version_string",
                      str(getattr(mutagen, "version", "installed"))))
    except Exception as e:  # noqa: BLE001
        check("mutagen", False, f"missing ({e})")
    try:
        import typer  # noqa: F401
        check("typer", True, "installed")
    except Exception as e:  # noqa: BLE001
        check("typer", False, f"missing ({e})")
    if convert_mod.ffmpeg_available():
        check("ffmpeg", True, subprocess_check())
    else:
        check("ffmpeg", False, "not on PATH (needed for conversion)")
    from importlib import resources
    try:
        resources.files("mlib.assets").joinpath("editor.html").read_bytes()
        check("editor asset", True, "bundled")
    except Exception as e:  # noqa: BLE001
        check("editor asset", False, str(e))

    h = host or (ctx.obj or {}).get("host")
    if h:
        try:
            info = remote_mod.remote_doctor(h)
            lines.append(f"  · remote {h}: python {info.get('python')}, "
                         f"mutagen {info.get('mutagen')}, ffmpeg {info.get('ffmpeg')}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"  ⚠ remote {h}: unreachable ({e})")
            issues.append(f"remote {h}")
        if sync:
            remote_mod.sync_source(h)
            lines.append(f"  ✅ mlib package synced to {h}:~/mlib_src")

    head = f"mlib doctor" + (" — all good" if not issues else " — see ⚠ above")
    _emit(ctx, {"issues": issues}, head + "\n" + "\n".join(lines) if lines else head)


def subprocess_check() -> str:
    import subprocess
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        return (r.stdout.splitlines() or ["present"])[0][:60]
    except Exception:  # noqa: BLE001
        return "present"


if __name__ == "__main__":
    run()
