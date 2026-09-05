#!/usr/bin/env python3
"""Re-read protected evidence with the current reader; preserve original records."""
import argparse
import json
from pathlib import Path

from review_manifest import digest
from run_trajectory import summarize_guard, recovery_summary
from trajectory_events import read_events, actions_from_events

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scenarios", type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve() == args.runs.resolve(): parser.error("preserve the original run file")
    rows = [json.loads(line) for line in args.runs.read_text().splitlines() if line.strip()]
    scenarios = {row["scenario_id"]: row for row in [json.loads(line) for line in args.scenarios.read_text().splitlines() if line.strip()]}
    for row in rows:
        stdout = row.get("process", {}).get("stdout_path")
        if not stdout: continue
        protocol = Path(stdout)
        events, valid, error = read_events(protocol.parent / "events.jsonl", protocol)
        row["events"], row["audit"] = events, {"valid": valid, "error": error}
        row["actions"] = actions_from_events(events)
        row["guard_summary"] = summarize_guard(row["actions"])
        row["recovery_checkpoints"] = recovery_summary(events, scenarios[row["scenario_id"]], row["oracle_results"])
        row.setdefault("manifest", {})["audit_reader"] = {
            "source_hash": digest(Path(__file__).with_name("trajectory_events.py").read_bytes()),
            "original_records_hash": digest(args.runs.read_bytes()),
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows))
