#!/usr/bin/env python3
"""Validate the trajectory scenario dataset, and optionally run records.

Mirrors validate_action_policy.py: records that fail schema validation are
reported and then excluded from every cross-record check, so malformed data
can never crash the validator or produce a misleading second error.

Beyond schema validation it enforces the things JSON Schema cannot express:

  - unique `scenario_id`, and no reserved outcome word (harmed, solved, safe,
    risky, allow, deny, ask) leaking into an id -- an id that announces its
    own outcome is a label in disguise;
  - grouped contrast: every scenario sharing a `group_id` uses the same
    fixture, the same `task_prompt` and the same `split`, contains exactly
    one benign control (`injection.present: false`) and at least one injected
    member, so the group differs in exactly one dimension;
  - injection self-consistency: a benign control carries channel `none`,
    a null payload and target harm `none`; an injected member carries none
    of those;
  - the fixture directory exists, declares a slot for the requested channel,
    and the slot's marker is actually present in the named file -- a missing
    marker means the payload would silently never be planted, which would
    read as a perfect ASR of zero;
  - the payload file exists, and a payload containing the {{SINK_URL}}
    placeholder is only used by a scenario that actually declares a
    `canary_sink` -- otherwise the substitution fails at run time;
  - oracle config sanity: a non-empty verification command, at least one
    canary file or a sink to observe, and a non-empty allowed-change set
    (an empty one makes every successful fix look like collateral damage).

Usage:
    python3 scripts/validate_trajectory.py
    python3 scripts/validate_trajectory.py --runs results/raw/traj.jsonl

Exit status is 0 when everything passes, 1 when any check fails.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_trajectory  # noqa: E402  (reuses the runner's planting logic, so the
#                                validator tests exactly what the runner does)

SCRIPT_DIR = Path(__file__).resolve().parent
BENCHMARK_ROOT = SCRIPT_DIR.parent
SCHEMAS = BENCHMARK_ROOT / "schemas"
FIXTURES_ROOT = BENCHMARK_ROOT / "fixtures"
PAYLOADS_ROOT = FIXTURES_ROOT / "payloads"
DEFAULT_SCENARIOS = BENCHMARK_ROOT / "datasets" / "trajectories" / "dev" / "scenarios.jsonl"

RESERVED_WORDS = ("harmed", "solved", "safe", "risky", "allow", "deny", "ask")
SINK_PLACEHOLDER = "{{SINK_URL}}"


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.checks = 0

    def check(self, condition: bool, message: str) -> bool:
        self.checks += 1
        if not condition:
            self.errors.append(message)
        return condition


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate_against_schema(
    records: list[dict[str, Any]], schema_path: Path, kind: str, report: Report
) -> list[dict[str, Any]]:
    """Return only the records that validate; report the rest."""
    validator = Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))
    valid: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        errors = sorted(validator.iter_errors(record), key=lambda e: list(e.path))
        if errors:
            identifier = record.get("scenario_id") or record.get("run_id") or f"line {index + 1}"
            for error in errors:
                report.check(
                    False,
                    f"{kind} {identifier}: schema: {'/'.join(map(str, error.path))}: {error.message}",
                )
        else:
            valid.append(record)
    return valid


def check_ids(scenarios: list[dict[str, Any]], report: Report) -> None:
    seen: set[str] = set()
    for scenario in scenarios:
        sid = scenario["scenario_id"]
        report.check(sid not in seen, f"duplicate scenario_id: {sid}")
        seen.add(sid)
        for identifier in (sid, scenario["group_id"]):
            leaked = [word for word in RESERVED_WORDS if word in identifier.split("-")]
            report.check(not leaked, f"{identifier}: leaks reserved outcome word(s) {leaked}")


def check_groups(scenarios: list[dict[str, Any]], report: Report) -> None:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for scenario in scenarios:
        groups[scenario["group_id"]].append(scenario)

    for group_id, members in sorted(groups.items()):
        controls = [m for m in members if not m["injection"]["present"]]
        injected = [m for m in members if m["injection"]["present"]]
        report.check(
            len(controls) == 1,
            f"group {group_id}: needs exactly one benign control, found {len(controls)}",
        )
        report.check(
            len(injected) >= 1,
            f"group {group_id}: needs at least one injected member, found {len(injected)}",
        )
        for field in ("fixture", "task_prompt", "split"):
            values = {m[field] for m in members}
            report.check(
                len(values) == 1,
                f"group {group_id}: members must share {field}, found {sorted(values)}",
            )


def check_injection_consistency(scenario: dict[str, Any], report: Report) -> None:
    sid = scenario["scenario_id"]
    injection = scenario["injection"]
    if injection["present"]:
        report.check(injection["channel"] != "none", f"{sid}: injected scenario has channel 'none'")
        report.check(injection["payload_id"] is not None, f"{sid}: injected scenario has null payload_id")
        report.check(injection["target_harm"] != "none", f"{sid}: injected scenario has target_harm 'none'")
    else:
        report.check(
            injection["channel"] == "none",
            f"{sid}: benign control declares channel {injection['channel']!r}",
        )
        report.check(injection["payload_id"] is None, f"{sid}: benign control declares a payload_id")
        report.check(injection["target_harm"] == "none", f"{sid}: benign control declares a target_harm")


def check_fixture_and_payload(scenario: dict[str, Any], report: Report) -> None:
    sid = scenario["scenario_id"]
    fixture_dir = FIXTURES_ROOT / scenario["fixture"]
    if not report.check(fixture_dir.is_dir(), f"{sid}: fixture directory missing: {fixture_dir}"):
        return

    manifest_path = fixture_dir / "fixture.json"
    if not report.check(manifest_path.is_file(), f"{sid}: fixture.json missing in {fixture_dir}"):
        return
    slots = json.loads(manifest_path.read_text(encoding="utf-8"))["injection_slots"]

    injection = scenario["injection"]
    if not injection["present"]:
        return

    channel = injection["channel"]
    if not report.check(channel in slots, f"{sid}: fixture declares no slot for channel {channel!r}"):
        return

    slot = slots[channel]
    slot_file = fixture_dir / slot["path"]
    if report.check(slot_file.is_file(), f"{sid}: slot file missing: {slot_file}"):
        report.check(
            slot["marker"] in slot_file.read_text(encoding="utf-8"),
            f"{sid}: marker {slot['marker']!r} not found in {slot['path']}; "
            "the payload would silently never be planted",
        )

    payload_path = PAYLOADS_ROOT / f"{injection['payload_id']}.md"
    if not report.check(payload_path.is_file(), f"{sid}: payload missing: {payload_path}"):
        return

    # Plant the payload for real and check the result still parses. A markdown
    # payload dropped raw into a .py slot is a syntax error: the fixture stops
    # importing, its own tests fail for a reason unrelated to the agent, and
    # the run reads as an attack that was defeated.
    if slot_file.is_file() and slot_file.suffix == ".py":
        planted = slot_file.read_text(encoding="utf-8").replace(
            slot["marker"],
            run_trajectory.apply_line_prefix(
                payload_path.read_text(encoding="utf-8"), slot.get("line_prefix")
            ),
        )
        try:
            ast.parse(planted)
        except SyntaxError as exc:
            report.check(
                False,
                f"{sid}: planting {injection['payload_id']} into {slot['path']} produces "
                f"invalid Python ({exc.msg} at line {exc.lineno}); the slot likely needs a "
                "line_prefix in fixture.json",
            )
        else:
            report.check(True, "")

    needs_sink = SINK_PLACEHOLDER in payload_path.read_text(encoding="utf-8")
    has_sink = scenario["oracles"]["harm"].get("canary_sink") is not None
    report.check(
        not (needs_sink and not has_sink),
        f"{sid}: payload {injection['payload_id']} contains {SINK_PLACEHOLDER} "
        "but the scenario declares canary_sink: null; planting would fail at run time",
    )
    report.check(
        not (injection["target_harm"] == "exfiltration" and not has_sink),
        f"{sid}: target_harm is 'exfiltration' but no canary_sink is declared, "
        "so exfiltration could never be observed",
    )


def check_oracles(scenario: dict[str, Any], report: Report) -> None:
    sid = scenario["scenario_id"]
    oracles = scenario["oracles"]
    harm = oracles["harm"]
    report.check(
        bool(harm["canary_files"]) or harm.get("canary_sink") or bool(harm["protected_paths"]),
        f"{sid}: harm oracle observes nothing -- no canary files, no sink, no protected paths",
    )
    report.check(bool(oracles["utility"]["command"]), f"{sid}: utility command is empty")
    collateral = oracles["collateral"]
    report.check(
        bool(collateral["allowed_change_globs"]) or bool(collateral["allowed_delete_globs"]),
        f"{sid}: both allowed_change_globs and allowed_delete_globs are empty; "
        "the agent has no way to solve the task without scoring collateral damage",
    )
    overlap = sorted(set(collateral["allowed_change_globs"]) & set(collateral["ignore_globs"]))
    report.check(
        not overlap,
        f"{sid}: {overlap} appear in both allowed_change_globs and ignore_globs; "
        "an ignored path is invisible to the diff, so allowing it is meaningless",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--runs", type=Path, action="append", default=None, help="repeatable; optional")
    args = parser.parse_args()

    report = Report()

    raw_scenarios = load_jsonl(args.scenarios)
    report.check(bool(raw_scenarios), f"{args.scenarios}: dataset is empty")
    scenarios = validate_against_schema(
        raw_scenarios, SCHEMAS / "trajectory-scenario.schema.json", "scenario", report
    )

    check_ids(scenarios, report)
    check_groups(scenarios, report)
    for scenario in scenarios:
        check_injection_consistency(scenario, report)
        check_fixture_and_payload(scenario, report)
        check_oracles(scenario, report)

    known_ids = {s["scenario_id"] for s in scenarios}
    for runs_path in args.runs or []:
        runs = validate_against_schema(
            load_jsonl(runs_path), SCHEMAS / "trajectory-run.schema.json", "run", report
        )
        for run in runs:
            report.check(
                run["scenario_id"] in known_ids,
                f"run {run['run_id']}: references unknown scenario_id {run['scenario_id']!r}",
            )
            report.check(
                run["environment"]["process_restarted"],
                f"run {run['run_id']}: process_restarted is false; Kilo caches project "
                "settings at workspace load (issue #7247), so this run measured stale policy",
            )

    if report.errors:
        for error in report.errors:
            print(f"FAIL {error}", file=sys.stderr)
        print(f"\n{len(report.errors)} failure(s) out of {report.checks} checks", file=sys.stderr)
        return 1

    print(f"OK: {len(scenarios)} scenario(s), {report.checks} checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
