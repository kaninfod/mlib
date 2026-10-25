"""validate — read-only compliance report for a plan (design §2).

A validated album is: M1 layout-conform folder + files, M2 minimum tags on
every file, M3 album consistency, M4 cover.jpg present, all files mp3.
Issue strings emitted by catalog carry category prefixes
(layout/tag/format/cover/dupe) — validate groups them; nothing here touches
files.
"""
from __future__ import annotations

from . import model

CATEGORIES = ("layout", "tag", "format", "cover", "dupe")


def _issue_category(issue: str) -> str:
    return issue.split(":", 1)[0] if ":" in issue else "other"


def validate_report(plan: dict, show_clean: bool = False) -> dict:
    albums = plan.get("albums", [])
    by_cat = {cat: 0 for cat in CATEGORIES}
    other = 0
    flagged = []
    for alb in albums:
        issues = list(alb.get("issues", []))
        if issues:
            flagged.append({
                "path": alb.get("path"),
                "tracks": len(alb.get("tracks", [])),
                "issues": issues,
            })
            for issue in issues:
                cat = _issue_category(issue)
                if cat in by_cat:
                    by_cat[cat] += 1
                else:
                    other += 1
    counts = model.plan_counts(plan)
    report = {
        "meta": plan.get("_meta", {}),
        "albums": counts["albums"],
        "tracks": counts["tracks"],
        "albums_flagged": len(flagged),
        "albums_clean": counts["albums"] - len(flagged),
        "issues_by_category": dict(sorted(by_cat.items())) | ({"other": other} if other else {}),
        "album_list": flagged,
    }
    if show_clean:
        report["albums_clean_list"] = [a["path"] for a in albums if not a.get("issues")]
    return report


def render_report(report: dict) -> str:
    lines: list[str] = []
    by = report["issues_by_category"]
    cat_summary = " · ".join(f"{cat}: {n}" for cat, n in by.items() if n)
    lines.append(f"mlib validate — {report['albums']} albums, {report['tracks']} tracks")
    if report["albums_flagged"] == 0:
        lines.append("  ✅ all albums conform to the standard")
    else:
        lines.append(f"  ✅ {report['albums_clean']} clean · "
                     f"⚠ {report['albums_flagged']} flagged ({cat_summary})")
        for alb in report["album_list"]:
            lines.append(f"\n  ⚠ {alb['path']}  ({alb['tracks']} tracks)")
            for issue in alb["issues"]:
                lines.append(f"     - {issue}")
    return "\n".join(lines)