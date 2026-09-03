#!/usr/bin/env python3
"""Validate the action-policy benchmark cases and labels.

This script checks that:
  1. Both JSON schemas are themselves valid Draft 2020-12 schemas.
  2. Every non-empty line of the cases/labels JSONL files parses as JSON
     and conforms to the matching schema. Records that fail schema
     validation are reported but excluded from every check below so that
     malformed data (e.g. case_id being a list instead of a string) can
     never crash a later check with a traceback.
  3. Neither the cases file nor the labels file is empty.
  4. There are no duplicate case IDs within the cases file, and no
     duplicate case IDs within the labels file (checked on schema-valid
     records only).
  5. Every case has exactly one matching label and vice versa (a strict
     one-to-one join on case_id).
  6. All cases sharing a group_id use the same split (dev/holdout never
     mixed within a paired group).
  7. Neither case_id nor group_id contains a hyphen-separated segment
     equal to 'safe', 'risky', 'allow', 'deny', or 'ask' -- those words
     are reserved for ground-truth labels and must never leak into
     identifiers a policy under evaluation might see.
  8. Every case's recent_actions (if present) has strictly increasing,
     non-duplicate sequence numbers (maxItems is enforced by the schema;
     this ordering constraint is not).
  9. Every group_id has at least one label with expected_decision
     'ALLOW', at least one label with expected_decision 'ASK' or 'DENY',
     and exactly one shared contrast_dimension across its members.
  10. Controlled contrasts are enforced per contrast_dimension:
        - action: identical user_intent and trusted_context across the
          group, with at least two distinct *normalized* proposed
          actions;
        - context: identical user_intent and proposed_action, with at
          least two distinct trusted_context values;
        - intent: identical trusted_context and proposed_action, with
          at least two distinct user_intent values;
        - mixed: at least two of user_intent, trusted_context, and the
          normalized proposed action differ across the group.
      Proposed-action distinctness (for 'action' and 'mixed') is judged
      on the curated normalized operation/targets/options, not on
      raw_tool_call text -- this suite never checks that a real
      normalizer would reproduce the same normalized action from a raw
      tool call (see the trajectories suite for that).

Usage:
    python3 validate_action_policy.py [--cases PATH] [--labels PATH]

Exit status is 0 when everything validates, non-zero otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCRIPT_DIR = Path(__file__).resolve().parent
BENCHMARK_ROOT = SCRIPT_DIR.parent
SCHEMAS_DIR = BENCHMARK_ROOT / "schemas"
DEFAULT_CASES_PATH = BENCHMARK_ROOT / "datasets" / "action-policy" / "dev" / "cases.jsonl"
DEFAULT_LABELS_PATH = BENCHMARK_ROOT / "datasets" / "action-policy" / "dev" / "labels.jsonl"

CASE_SCHEMA_PATH = SCHEMAS_DIR / "action-case.schema.json"
LABEL_SCHEMA_PATH = SCHEMAS_DIR / "action-label.schema.json"

# Words that encode ground truth and must never appear as a hyphen-separated
# segment of a case_id or group_id.
RESERVED_ID_SEGMENTS = {"safe", "risky", "allow", "deny", "ask"}

Record = tuple[int, dict[str, Any]]


class ValidationReport:
    """Collects errors so the script can report everything in one pass."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def add(self, message: str) -> None:
        self.errors.append(message)

    @property
    def ok(self) -> bool:
        return not self.errors


def load_json_file(path: Path, report: ValidationReport) -> dict[str, Any] | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        report.add(f"{path}: file not found")
    except json.JSONDecodeError as exc:
        report.add(f"{path}: invalid JSON ({exc.msg} at line {exc.lineno})")
    return None


def load_and_check_schema(path: Path, report: ValidationReport) -> dict[str, Any] | None:
    """Load a JSON Schema document and verify it is a valid Draft 2020-12 schema."""
    schema = load_json_file(path, report)
    if schema is None:
        return None
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:  # jsonschema raises SchemaError subclasses of Exception
        report.add(f"{path}: not a valid Draft 2020-12 schema ({exc})")
        return None
    return schema


def load_jsonl_records(path: Path, report: ValidationReport) -> list[Record]:
    """Read a JSONL file, skipping blank lines, reporting malformed lines by number."""
    records: list[Record] = []
    if not path.exists():
        report.add(f"{path}: file not found")
        return records
    with path.open("r", encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                report.add(f"{path}:{line_no}: invalid JSON ({exc.msg})")
                continue
            if not isinstance(record, dict):
                report.add(f"{path}:{line_no}: record is not a JSON object")
                continue
            records.append((line_no, record))
    return records


def require_non_empty_dataset(path: Path, records: list[Record], report: ValidationReport) -> bool:
    """Reject datasets with zero usable records. Returns True when non-empty."""
    if not records:
        report.add(f"{path}: dataset must not be empty (no records found)")
        return False
    return True


def _error_sort_key(error: Any) -> list[str]:
    """Sort key that never compares mixed path element types (e.g. int vs str)."""
    return [str(p) for p in error.path]


def validate_records(
    path: Path,
    records: list[Record],
    validator: Draft202012Validator,
    report: ValidationReport,
) -> list[Record]:
    """Validate every record against the schema.

    Returns only the records that passed schema validation, so that every
    downstream cross-record check operates on well-typed data and can never
    raise (e.g. from hashing or comparing an unexpected type).
    """
    valid: list[Record] = []
    for line_no, record in records:
        errors = list(validator.iter_errors(record))
        if errors:
            for error in sorted(errors, key=_error_sort_key):
                location = "/".join(str(p) for p in error.path) or "<root>"
                report.add(f"{path}:{line_no}: {location}: {error.message}")
        else:
            valid.append((line_no, record))
    return valid


def find_duplicate_ids(
    path: Path,
    records: list[Record],
    key: str,
    report: ValidationReport,
) -> None:
    seen: dict[Any, int] = {}
    for line_no, record in records:
        value = record.get(key)
        if value is None:
            continue
        if value in seen:
            report.add(
                f"{path}:{line_no}: duplicate {key} '{value}' "
                f"(first seen at line {seen[value]})"
            )
        else:
            seen[value] = line_no


def check_reserved_id_segments(
    path: Path,
    records: list[Record],
    field: str,
    report: ValidationReport,
) -> None:
    """Reject case_id/group_id values with a segment equal to a reserved label word."""
    for line_no, record in records:
        value = record.get(field)
        if not isinstance(value, str):
            continue
        segments = value.split("-")
        hits = sorted({segment for segment in segments if segment in RESERVED_ID_SEGMENTS})
        if hits:
            report.add(
                f"{path}:{line_no}: {field} '{value}' contains reserved segment(s) {hits}; "
                "'safe', 'risky', 'allow', 'deny', and 'ask' are reserved for ground-truth "
                "labels and must not appear in case/group identifiers"
            )


def check_case_label_join(
    cases_path: Path,
    labels_path: Path,
    cases: list[Record],
    labels: list[Record],
    report: ValidationReport,
) -> None:
    case_ids = {record["case_id"] for _, record in cases if isinstance(record.get("case_id"), str)}
    label_ids = {record["case_id"] for _, record in labels if isinstance(record.get("case_id"), str)}

    for missing in sorted(case_ids - label_ids):
        report.add(f"{cases_path}: case_id '{missing}' has no matching label in {labels_path}")
    for missing in sorted(label_ids - case_ids):
        report.add(f"{labels_path}: case_id '{missing}' has no matching case in {cases_path}")


def check_group_split_consistency(
    cases_path: Path,
    cases: list[Record],
    report: ValidationReport,
) -> None:
    group_splits: dict[str, tuple[str, int]] = {}
    for line_no, record in cases:
        group_id = record.get("group_id")
        split = record.get("split")
        if not isinstance(group_id, str) or not isinstance(split, str):
            continue
        if group_id in group_splits:
            expected_split, first_line = group_splits[group_id]
            if split != expected_split:
                report.add(
                    f"{cases_path}:{line_no}: group_id '{group_id}' has split "
                    f"'{split}' but line {first_line} used split '{expected_split}'"
                )
        else:
            group_splits[group_id] = (split, line_no)


def check_recent_actions_sequences(
    path: Path,
    cases: list[Record],
    report: ValidationReport,
) -> None:
    """Within each case's recent_actions, sequence numbers must be strictly
    increasing with no duplicates (oldest to newest). maxItems is enforced by
    the JSON schema; this ordering constraint is not expressible there, so it
    is checked here instead.
    """
    for line_no, record in cases:
        input_obj = record.get("input")
        recent_actions = input_obj.get("recent_actions") if isinstance(input_obj, dict) else None
        if not isinstance(recent_actions, list):
            continue
        previous_sequence: int | None = None
        for entry in recent_actions:
            if not isinstance(entry, dict):
                continue
            sequence = entry.get("sequence")
            if not isinstance(sequence, int):
                continue
            if previous_sequence is not None and sequence <= previous_sequence:
                report.add(
                    f"{path}:{line_no}: case_id '{record.get('case_id')}': "
                    "recent_actions sequence values must be strictly increasing "
                    f"with no duplicates, but {sequence} follows {previous_sequence}"
                )
            previous_sequence = sequence


def _user_intent_key(case: dict[str, Any]) -> Any:
    return case.get("input", {}).get("user_intent")


def _trusted_context_key(case: dict[str, Any]) -> str:
    return json.dumps(case.get("input", {}).get("trusted_context"), sort_keys=True)


def _proposed_action_key(case: dict[str, Any]) -> str:
    """Full proposed_action (raw_tool_call + normalized). Used where the
    contrast requires the action to stay identical (context/intent).
    """
    return json.dumps(case.get("input", {}).get("proposed_action"), sort_keys=True)


def _normalized_action_key(case: dict[str, Any]) -> str:
    """Only the curated normalized action, used to judge *semantic* action
    variation (action/mixed). Two records can share this key while differing
    in raw_tool_call text, and vice versa -- this suite never validates that
    a real normalizer would derive one from the other (see module docstring).
    """
    proposed_action = case.get("input", {}).get("proposed_action")
    normalized = proposed_action.get("normalized") if isinstance(proposed_action, dict) else None
    return json.dumps(normalized, sort_keys=True)


GroupMember = tuple[str, dict[str, Any], dict[str, Any]]


def _check_action_contrast(group_id: str, members: list[GroupMember], report: ValidationReport) -> None:
    intents = {_user_intent_key(case) for _, case, _ in members}
    if len(intents) > 1:
        report.add(
            f"group '{group_id}': contrast_dimension 'action' requires identical "
            "user_intent across the group"
        )
    contexts = {_trusted_context_key(case) for _, case, _ in members}
    if len(contexts) > 1:
        report.add(
            f"group '{group_id}': contrast_dimension 'action' requires identical "
            "trusted_context across the group"
        )
    normalized_actions = {_normalized_action_key(case) for _, case, _ in members}
    if len(normalized_actions) < 2:
        report.add(
            f"group '{group_id}': contrast_dimension 'action' requires at least two "
            "distinct normalized proposed actions across the group"
        )


def _check_context_contrast(group_id: str, members: list[GroupMember], report: ValidationReport) -> None:
    intents = {_user_intent_key(case) for _, case, _ in members}
    if len(intents) > 1:
        report.add(
            f"group '{group_id}': contrast_dimension 'context' requires identical "
            "user_intent across the group"
        )
    actions = {_proposed_action_key(case) for _, case, _ in members}
    if len(actions) > 1:
        report.add(
            f"group '{group_id}': contrast_dimension 'context' requires an identical "
            "proposed_action across the group"
        )
    contexts = {_trusted_context_key(case) for _, case, _ in members}
    if len(contexts) < 2:
        report.add(
            f"group '{group_id}': contrast_dimension 'context' requires at least two "
            "distinct trusted_context values across the group"
        )


def _check_intent_contrast(group_id: str, members: list[GroupMember], report: ValidationReport) -> None:
    contexts = {_trusted_context_key(case) for _, case, _ in members}
    if len(contexts) > 1:
        report.add(
            f"group '{group_id}': contrast_dimension 'intent' requires identical "
            "trusted_context across the group"
        )
    actions = {_proposed_action_key(case) for _, case, _ in members}
    if len(actions) > 1:
        report.add(
            f"group '{group_id}': contrast_dimension 'intent' requires an identical "
            "proposed_action across the group"
        )
    intents = {_user_intent_key(case) for _, case, _ in members}
    if len(intents) < 2:
        report.add(
            f"group '{group_id}': contrast_dimension 'intent' requires at least two "
            "distinct user_intent values across the group"
        )


def _check_mixed_contrast(group_id: str, members: list[GroupMember], report: ValidationReport) -> None:
    intents = {_user_intent_key(case) for _, case, _ in members}
    contexts = {_trusted_context_key(case) for _, case, _ in members}
    normalized_actions = {_normalized_action_key(case) for _, case, _ in members}
    varying = sum(1 for values in (intents, contexts, normalized_actions) if len(values) > 1)
    if varying < 2:
        report.add(
            f"group '{group_id}': contrast_dimension 'mixed' requires at least two of "
            "user_intent, trusted_context, and the normalized proposed action to differ "
            "across the group"
        )


_CONTRAST_DIMENSION_CHECKS: dict[str, Any] = {
    "action": _check_action_contrast,
    "context": _check_context_contrast,
    "intent": _check_intent_contrast,
    "mixed": _check_mixed_contrast,
}


def check_groups(
    cases_path: Path,
    labels_path: Path,
    cases: list[Record],
    labels: list[Record],
    report: ValidationReport,
) -> None:
    """Per-group_id checks that need both cases and labels joined by case_id."""
    case_by_id: dict[str, dict[str, Any]] = {
        record["case_id"]: record for _, record in cases if isinstance(record.get("case_id"), str)
    }
    label_by_id: dict[str, dict[str, Any]] = {
        record["case_id"]: record for _, record in labels if isinstance(record.get("case_id"), str)
    }

    groups: dict[str, list[str]] = {}
    for _, record in cases:
        group_id = record.get("group_id")
        case_id = record.get("case_id")
        if not isinstance(group_id, str) or not isinstance(case_id, str):
            continue
        groups.setdefault(group_id, []).append(case_id)

    for group_id in sorted(groups):
        members: list[GroupMember] = []
        for case_id in groups[group_id]:
            case = case_by_id.get(case_id)
            label = label_by_id.get(case_id)
            if case is None or label is None:
                # Missing/duplicate join issues are already reported elsewhere.
                continue
            members.append((case_id, case, label))

        if not members:
            continue

        decisions = {label.get("expected_decision") for _, _, label in members}
        if "ALLOW" not in decisions:
            report.add(
                f"group '{group_id}': requires at least one case with expected_decision "
                "'ALLOW'"
            )
        if not decisions & {"ASK", "DENY"}:
            report.add(
                f"group '{group_id}': requires at least one case with expected_decision "
                "'ASK' or 'DENY'"
            )

        contrast_dims = {case.get("contrast_dimension") for _, case, _ in members}
        if len(contrast_dims) != 1:
            report.add(
                f"group '{group_id}': all cases must share exactly one contrast_dimension, "
                f"found {sorted(d for d in contrast_dims if isinstance(d, str))}"
            )
            continue

        dimension = next(iter(contrast_dims))
        check_fn = _CONTRAST_DIMENSION_CHECKS.get(dimension) if isinstance(dimension, str) else None
        if check_fn is not None:
            check_fn(group_id, members, report)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help=f"Path to cases JSONL file (default: {DEFAULT_CASES_PATH})",
    )
    parser.add_argument(
        "--labels",
        type=Path,
        default=DEFAULT_LABELS_PATH,
        help=f"Path to labels JSONL file (default: {DEFAULT_LABELS_PATH})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = ValidationReport()

    case_schema = load_and_check_schema(CASE_SCHEMA_PATH, report)
    label_schema = load_and_check_schema(LABEL_SCHEMA_PATH, report)

    valid_cases: list[Record] = []
    valid_labels: list[Record] = []

    if case_schema is not None:
        raw_cases = load_jsonl_records(args.cases, report)
        if require_non_empty_dataset(args.cases, raw_cases, report):
            case_validator = Draft202012Validator(case_schema)
            valid_cases = validate_records(args.cases, raw_cases, case_validator, report)
            find_duplicate_ids(args.cases, valid_cases, "case_id", report)
            check_group_split_consistency(args.cases, valid_cases, report)
            check_reserved_id_segments(args.cases, valid_cases, "case_id", report)
            check_reserved_id_segments(args.cases, valid_cases, "group_id", report)
            check_recent_actions_sequences(args.cases, valid_cases, report)

    if label_schema is not None:
        raw_labels = load_jsonl_records(args.labels, report)
        if require_non_empty_dataset(args.labels, raw_labels, report):
            label_validator = Draft202012Validator(label_schema)
            valid_labels = validate_records(args.labels, raw_labels, label_validator, report)
            find_duplicate_ids(args.labels, valid_labels, "case_id", report)
            check_reserved_id_segments(args.labels, valid_labels, "case_id", report)

    if valid_cases and valid_labels:
        check_case_label_join(args.cases, args.labels, valid_cases, valid_labels, report)
        check_groups(args.cases, args.labels, valid_cases, valid_labels, report)

    if not report.ok:
        print("action-policy validation FAILED:", file=sys.stderr)
        for message in report.errors:
            print(f"  - {message}", file=sys.stderr)
        return 1

    print(
        "action-policy validation OK: "
        f"{len(valid_cases)} case(s) in {args.cases}, "
        f"{len(valid_labels)} label(s) in {args.labels}, "
        "1:1 case-label join verified, group splits consistent, "
        "each group has an ALLOW control plus an ASK/DENY case sharing one "
        "controlled contrast_dimension."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
