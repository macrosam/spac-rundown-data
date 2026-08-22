#!/usr/bin/env python3
"""Verify every non-media EDGAR path in latest.json exists in history.json.

Exits nonzero when coverage is incomplete, blocking a silent history regression.
"""
import json
import sys

SECTIONS = ("pipeline", "pricings", "amends", "mergers",
            "votes", "pipes", "sponsors", "exchanges")


def main() -> int:
    try:
        latest = json.load(open("data/latest.json"))
        history = json.load(open("data/history.json"))["rows"]
    except Exception as exc:
        print(f"FAIL: cannot load feed files: {exc}", file=sys.stderr)
        return 1

    missing = []
    for sec in SECTIONS:
        for rec in latest.get(sec, []):
            p = rec.get("path")
            if p and p not in history:
                missing.append(p)

    if missing:
        print(f"FAIL: {len(missing)} filing paths in latest.json absent from history.json",
              file=sys.stderr)
        for p in missing[:10]:
            print(f"  {p}", file=sys.stderr)
        return 1

    print(f"OK: all {sum(len(latest.get(s, [])) for s in SECTIONS)} filing paths covered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
