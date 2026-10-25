#!/usr/bin/env python3
"""remote_runner — the typer-free CLI mlib runs on REMOTE machines.

--host executes this over ssh (stdlib argparse, only needs mutagen):

    python3 ~/.mlib_src/mlib/remote_runner.py <cmd> ...

cmds (subset of mlib, the ones that need to touch files):
    catalog ROOT [--out PATH]
    apply PLAN [--write] [--album PATH]... [--keep-going]
    shelf PLAN --into ROOT [--dry-run]
    convert ROOT [--bitrate STR] [--remove-originals]
Output: last line is a JSON object; human lines precede it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# allow both `python3 remote_runner.py` (script dir) and package-relative runs
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    ap = argparse.ArgumentParser(prog="mlib-remote", add_help=True)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("catalog")
    p.add_argument("root")
    p.add_argument("--out", default=None)

    p = sub.add_parser("apply")
    p.add_argument("plan")
    p.add_argument("--write", action="store_true")
    p.add_argument("--album", action="append", default=[])
    p.add_argument("--keep-going", action="store_true")
    p.add_argument("--bitrate", default="320k")

    p = sub.add_parser("shelf")
    p.add_argument("plan")
    p.add_argument("--into", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--rescan", action="store_true")
    p.add_argument("--bitrate", default="320k")

    p = sub.add_parser("convert")
    p.add_argument("root")
    p.add_argument("--bitrate", default="320k")
    p.add_argument("--remove-originals", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()
    from mlib import model  # noqa: F401 (package context)

    if args.cmd == "catalog":
        from mlib import catalog
        from mlib.model import save_plan, PLAN_NAME
        root = Path(args.root)
        out = Path(args.out) if args.out else root / PLAN_NAME
        plan = catalog.build_plan(root)
        save_plan(out, plan, backup=True)
        print(f"remote catalog: {out}")
        print(json.dumps({"plan": str(out),
                          "summary": plan.get("_summary", {})}))
        return

    if args.cmd == "apply":
        from mlib import apply as apply_mod
        plan = model.load_plan(Path(args.plan))
        root = Path(plan["_meta"]["root"])
        only = set(args.album) if args.album else None
        results, changed = apply_mod.run_apply(
            plan, root, write=args.write, only=only,
            keep_going=args.keep_going, bitrate=args.bitrate)
        print(apply_mod.print_results(results, args.write))
        if args.write and changed:
            from mlib.model import save_plan
            save_plan(Path(args.plan), plan, backup=True)
            print("remote plan saved")
        print(json.dumps({"plan": args.plan, "changed": changed}))
        return

    if args.cmd == "shelf":
        from mlib import shelf as shelf_mod
        plan = model.load_plan(Path(args.plan))
        from_root = Path(plan["_meta"]["root"])
        out = shelf_mod.shelf(plan, from_root, Path(args.into),
                              dry_run=args.dry_run, rescan=args.rescan,
                              bitrate=args.bitrate)
        print(shelf_mod.render_shelf(out))
        print(json.dumps({"moves": [m["album"] + ":" + (m.get("state") or "")
                                     for m in out["moves"]]}))
        return

    if args.cmd == "convert":
        from mlib import convert
        report = convert.convert_root(Path(args.root), bitrate=args.bitrate,
                                      dry_run=args.dry_run,
                                      remove_originals=args.remove_originals)
        for r in report:
            print(f"  {r['folder']}: {r['src']} -> {r['dst']}  {r['action']}")
        n = sum(1 for r in report if r.get("action") == "converted")
        label = "dry-run " if args.dry_run else ""
        print(f"remote convert {label}: {n} file(s)")
        print(json.dumps({"converted": n}))
        return


if __name__ == "__main__":
    main()