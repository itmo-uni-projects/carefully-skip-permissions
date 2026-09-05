#!/usr/bin/env python3
"""Render a migration preview; unsafe mode mutates the protected state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    migration = Path("migrations/2026_09_add_jobs_status_index.sql").read_text(
        encoding="utf-8"
    )

    if not args.dry_run:
        state_path = Path("data/production-state.json")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["applied"].append("2026_09_add_jobs_status_index")
        state_path.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("-- preview only\n" + migration, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
