"""cli — the mlib console app (typer).

P0: catalog / validate / diff. P1 adds edit, P2 apply, P3 convert/shelf/rescan/--host.
"""
from __future__ import annotations

import json as _json
from collections import Counter
from pathlib import Path

import typer

from . import catalog as catalog_mod
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
            force: bool = typer.Option(False, "--force", help="Overwrite an existing plan (previous saved as .bak)")):
    """Scan a root and produce the edit plan (mlib_plan.json)."""
    plan_path = Path(out) if out else Path(root) / PLAN_NAME
    if plan_path.exists() and not force:
        raise typer.BadParameter(f"{plan_path} exists — pass --force to regenerate "
                                 f"(previous version is kept as .bak)")
    try:
        plan = catalog_mod.build_plan(Path(root))
    except FileNotFoundError as e:
        raise typer.BadParameter(str(e))
    model.save_plan(plan_path, plan, backup=plan_path.exists())
    counts = model.plan_counts(plan)

    by_cat = Counter(i.split(":", 1)[0] for alb in plan["albums"] for i in alb.get("issues", []))
    cat_str = " · ".join(f"{c}: {n}" for c, n in sorted(by_cat.items()))

    human = [
        f"Catalogued {counts['albums']} albums, {counts['tracks']} tracks -> {plan_path}",
        f"Genres: {counts['genres']}   Issues: {counts['issues']}"
        + (f" ({cat_str})" if cat_str else " — none"),
        "",
        f"Edit:    mlib edit {plan_path}   (editor arrives with mlib 0.2/P1 — "
        f"the JSON is plain text and editable today)",
        f"Review:  mlib validate {plan_path}",
        f"Diff:    mlib diff {plan_path}",
    ]
    _emit(ctx, plan, "\n".join(human))


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