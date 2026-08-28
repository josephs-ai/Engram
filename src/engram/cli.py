"""
Command-line front end for the engram package.

One entry point instead of knowing which of ~200 scripts to run:

    engram search "postgres watermark"
    engram recall --since 2d
    engram recall --since 3d "phase 4"
    engram health
"""
from __future__ import annotations

import argparse
import json
import sys

from . import health, recall, search


def _print_rows(rows: list[dict], as_json: bool) -> None:
    if as_json:
        print(json.dumps(rows, indent=2, default=str))
        return
    if not rows:
        print("  (nothing found)")
        return
    for r in rows:
        text = " ".join(str(r.get("text") or r.get("summary") or "").split())
        stamp = r.get("started_at") or r.get("event_at") or ""
        prefix = f"{stamp:%Y-%m-%d %H:%M}  " if hasattr(stamp, "year") else ""
        print(f"  {prefix}{text[:120]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="engram", description="Query agent memory.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="durable facts: what do we know about X")
    s.add_argument("query", nargs="+")
    s.add_argument("--limit", type=int, default=10)
    s.add_argument("--after", default=None, help="window start (2d, yesterday, ISO date)")
    s.add_argument("--before", default=None)

    r = sub.add_parser("recall", help="activity: what happened, and when")
    r.add_argument("query", nargs="*", help="optional words to narrow within the window")
    r.add_argument("--since", default="1d")
    r.add_argument("--until", default=None)
    r.add_argument("--agent", default=None)
    r.add_argument("--limit", type=int, default=20)

    sub.add_parser("health", help="is the store usable")

    args = ap.parse_args(argv)

    if args.cmd == "health":
        info = health()
        if args.json:
            print(json.dumps(info, indent=2))
        else:
            for k, v in info.items():
                print(f"  {k:14} {v}")
        # Non-zero when part of the store is invisible to search, so this is
        # usable as a readiness probe rather than only as a human report.
        return 0 if info["ok"] else 1

    if args.cmd == "search":
        _print_rows(search(" ".join(args.query), limit=args.limit,
                           after=args.after, before=args.before), args.json)
        return 0

    rows = recall(since=args.since, until=args.until,
                  query=" ".join(args.query) or None,
                  agent=args.agent, limit=args.limit)
    _print_rows(rows, args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
