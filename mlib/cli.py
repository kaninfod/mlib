"""cli — the mlib console app (typer).

P0: catalog / validate / diff. P1 adds edit, P2 apply, P3 convert/shelf/rescan/--host.
"""
from __future__ import annotations

import json as _json
from collections import Counter
from pathlib import Path

import typer

from . import apply as apply_mod
from . import catalog as catalog_mod
from . import edit as edit_mod
from . import model, validate as validate_mod
from .model import PLAN_NAME, PlanError

app = typer.Typer(add_completion=False,
                  help="mlib — file, tag and cover your music collection to one standard.")


def _emit(ctx: typer.Context, machine, human: str) -> None:
    """Print JSON (--json) or the human text."""
    if ctx.obj and ctx.obj.get("json_out"):
        typer.echo(_json.dumps(machine, ensure_ascii=False, indent=2))
    else:
        typer.echo(human)


def _resolve_plan(target: Path) -> Path:
    """Accept a plan file or a root directory (uses <root>/mlib_plan.json)."""
    target = Path(target)
    if target.is_dir():
        target = target / PLAN_NAME
    if not target.exists():
        raise typer.BadParameter(
            f"{target} not found — run 'mlib catalog <root>' first")
    return target


@app.callback()
def main(ctx: typer.Context,
         json_out: bool = typer.Option(False, "--json", help="Machine-readable output"),
         verbose: bool = typer.Option(False, "--verbose", "-v", help="More detail where applicable")):
    ctx.obj = {"json_out": json_out, "verbose": verbose}


@app.command()
def catalog(ctx: typer.Context,
            root: Path = typer.Argument(..., exists=True, file_okay=False,
                                        help="Folder to scan (collection, staging, any music root)"),
            out: Path = typer.Option(None, "--out", help="Plan path (default <root>/mlib_plan.json)"),
            force: bool = typer.Option(False, "--force", help="Overwrite an existing plan (previous saved as .bak); pending edits are carried over via merge"),
            open_ed: bool = typer.Option(False, "--open", help="Open the editor straight after cataloging (serves until Ctrl+C)")):
    """Scan a root and produce the edit plan (mlib_plan.json)."""
    plan_path = Path(out) if out else Path(root) / PLAN_NAME
    if plan_path.exists() and not force:
        raise typer.BadParameter(f"{plan_path} exists — pass --force to regenerate "
                                 f"(previous version is kept as .bak)")
    try:
        fresh = catalog_mod.build_plan(Path(root))
    except FileNotFoundError as e:
        raise typer.BadParameter(str(e))
    if force and plan_path.exists():
        try:
            old = model.load_plan(plan_path)
            fresh = model.merge_rescan(old, fresh)   # pending edits survive
            fresh["_meta"]["merged"] = True
        except PlanError:
            pass
    model.save_plan(plan_path, fresh, backup=plan_path.exists())
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
          target: Path = typer.Argument(..., help="Plan file or scanned root (uses <root>/mlib_plan.json)"),
          write: bool = typer.Option(False, "--write", help="Execute the changes (default: preview only)"),
          album: list[str] = typer.Option(None, "--album", help="Restrict to album path(s) — repeatable"),
          keep_going: bool = typer.Option(False, "--keep-going", help="Continue on album errors instead of stopping"),
          bitrate: str = typer.Option("320k", "--bitrate", help="mp3 bitrate for conversions (default 320k CBR)")):
    """Converge files toward the plan: convert, tag, cover, rename, clean.

    Preview by default — pass --write to actually change files.
    The plan is updated to the post-apply state (applied albums are refreshed
    in place); run `mlib catalog <root> --force` afterwards for a full refresh
    (pending edits survive the merge)."""
    plan_path = _resolve_plan(target)
    plan = model.load_plan(plan_path)
    root = Path(plan.get("_meta", {}).get("root") or plan_path.parent)
    if not root.is_dir():
        raise typer.BadParameter(f"plan root does not exist on this machine: "
                                 f"{root} — run apply where the files live "
                                 f"(P3 --host automates this)")
    only = set(album) if album else None
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
         target: Path = typer.Argument(..., help="Plan file or scanned root (uses <root>/mlib_plan.json)"),
         port: int = typer.Option(0, "--port", help="Bridge port (default: any free port)"),
         no_open: bool = typer.Option(False, "--no-open", help="Do not launch a browser, just serve")):
    """Edit a plan in your browser: localhost bridge + bundled static editor.

    Works in Firefox, Chromium and Safari. Edits autosave (server keeps .bak);
    Ctrl+C stops the bridge."""
    plan_path = _resolve_plan(target)
    model.load_plan(plan_path)  # fail fast on corrupt plans
    edit_mod.run_until_interrupt(plan_path, port=port, open_browser=not no_open)


@app.command()
def validate(ctx: typer.Context,
             target: Path = typer.Argument(..., help="Plan file or scanned root"),
             all_albums: bool = typer.Option(False, "--all", help="List clean albums too")):
    """Compliance report: what breaks the standard, per album."""
    plan = model.load_plan(_resolve_plan(target))
    report = validate_mod.validate_report(plan, show_clean=all_albums)
    report = {k: v for k, v in report.items() if v is not None}
    _emit(ctx, report, validate_mod.render_report(report))


@app.command()
def diff(ctx: typer.Context,
         target: Path = typer.Argument(..., help="Plan file or scanned root"),
         verbose: bool = typer.Option(False, "--verbose", "-v", help="Per-track edit lines")):
    """Show pending edits (new != current) before applying."""
    plan = model.load_plan(_resolve_plan(target))
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
                if ch.get("file"):
                    lines.append(f"  {ch['file']}")
                    lines.append(f"    {ch['field']}: '{fmt(ch['current'])}' -> '{fmt(ch['new'])}'")
                else:
                    lines.append(f"  {ch['field']}: '{fmt(ch['current'])}' -> '{fmt(ch['new'])}'")
        return "\n".join(lines)

    _emit(ctx, {"edits": counts["edits"], "albums": albums_pending, "changes": rows},
          render() if rows else "No pending edits — the plan matches disk.")


def run() -> None:
    try:
        app()
    except PlanError as e:
        typer.echo(f"error: {e}", err=True)
        raise SystemExit(2)


if __name__ == "__main__":
    run()