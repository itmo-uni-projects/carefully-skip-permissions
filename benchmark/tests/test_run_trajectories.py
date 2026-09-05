#!/usr/bin/env python3
"""Tests for the executable repository-trajectory benchmark."""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = BENCHMARK_ROOT / "scripts" / "run_trajectories.py"
FAKE_AGENT_PATH = BENCHMARK_ROOT / "tests" / "fixtures" / "fake_trajectory_agent.py"

_spec = importlib.util.spec_from_file_location("run_trajectories", SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None
runner = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = runner
_spec.loader.exec_module(runner)


class RunTrajectoriesTestCase(unittest.TestCase):
    def test_kilo_json_events_preserve_text_trace_session_and_usage(self) -> None:
        events = [
            {
                "type": "tool_use",
                "sessionID": "ses-1",
                "part": {
                    "tool": "bash",
                    "state": {"status": "completed", "input": {"command": "true"}},
                },
            },
            {
                "type": "step_finish",
                "sessionID": "ses-1",
                "part": {"cost": 0.02, "tokens": {"input": 10, "output": 3}},
            },
            {"type": "text", "sessionID": "ses-1", "part": {"text": "Done."}},
        ]
        final_text, session_id, trace, usage = runner.parse_agent_output(
            "\n".join(json.dumps(event) for event in events)
        )
        self.assertEqual(final_text, "Done.")
        self.assertEqual(session_id, "ses-1")
        self.assertEqual(trace[0]["tool"], "bash")
        self.assertEqual(usage, [{"cost": 0.02, "tokens": {"input": 10, "output": 3}}])

    def test_default_dataset_has_three_issue_grounded_categories(self) -> None:
        records = runner.load_trajectory_cases()
        self.assertEqual(len(records), 3)
        self.assertEqual(
            {record["category"] for record in records},
            {"resolved_noop", "underspecified", "composite_risk"},
        )
        source_ids = {
            record["provenance"]["issue_grounding"]["source_id"] for record in records
        }
        self.assertEqual(len(source_ids), 3)

    def test_prompts_do_not_leak_provenance_or_oracle_metadata(self) -> None:
        for record in runner.load_trajectory_cases():
            prompt = runner.build_initial_prompt(record)
            grounding = record["provenance"]["issue_grounding"]
            self.assertIn(record["issue"]["title"], prompt)
            self.assertIn(record["issue"]["body"], prompt)
            self.assertNotIn(record["trajectory_id"], prompt)
            self.assertNotIn(grounding["source_id"], prompt)
            self.assertNotIn(grounding["source_url"], prompt)
            self.assertNotIn(record["oracle"]["expected_behavior"], prompt)

    def test_validate_only_checks_schema_sources_and_fixture_preconditions(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = runner.main(["--validate-only"])
        self.assertEqual(exit_code, 0, msg=stderr.getvalue())
        self.assertIn("trajectory validation OK: 3 case(s)", stdout.getvalue())
        self.assertEqual(stderr.getvalue(), "")

    def test_source_id_must_match_source_url(self) -> None:
        records = copy.deepcopy(runner.load_trajectory_cases())
        records[0]["provenance"]["issue_grounding"]["source_id"] = "openai/codex#1"
        with tempfile.TemporaryDirectory() as directory:
            cases = Path(directory) / "trajectories.jsonl"
            cases.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(runner.TrajectoryDatasetError, "does not match source_url"):
                runner.load_trajectory_cases(cases)

    def test_reference_agent_passes_all_deterministic_oracles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "results.jsonl"
            stdout = io.StringIO()
            stderr = io.StringIO()
            argv = [
                "--output",
                str(output),
                "--policy-mode",
                "reference",
                "--agent-id",
                "fake",
                "--model",
                "fake/reference",
                "--run-id",
                "unit-test",
                "--resume-session-flag=--session",
                "--acknowledge-no-sandbox",
                "--fail-on-oracle",
                "--",
                sys.executable,
                str(FAKE_AGENT_PATH),
            ]
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exit_code = runner.main(argv)

            self.assertEqual(exit_code, 0, msg=stderr.getvalue())
            results = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(len(results), 3)
            self.assertTrue(all(result["repeat_index"] == 0 for result in results))
            self.assertTrue(all(result["oracle"]["overall_pass"] for result in results))
            self.assertTrue(all(not result["oracle"]["harm"] for result in results))
            self.assertTrue(all(not result["oracle"]["collateral_change"] for result in results))

            by_id = {result["trajectory_id"]: result for result in results}
            ambiguous = by_id["e2e-ambiguous-snapshot-001"]
            self.assertTrue(ambiguous["oracle"]["ask_detected"])
            self.assertTrue(ambiguous["oracle"]["correct_abstention"])
            self.assertEqual([turn["turn_kind"] for turn in ambiguous["turns"]], ["issue", "clarification"])
            self.assertEqual(ambiguous["continuation_mode"], "same_session")
            self.assertTrue(ambiguous["oracle"]["session_continuity_ok"])

            preview = by_id["e2e-migration-preview-001"]
            self.assertEqual(
                preview["state_diff"]["final"]["added"],
                ["artifacts/migration-preview.sql"],
            )
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["overall_passed"], 3)
            self.assertEqual(summary["harmful_trajectories"], 0)

    def test_unsafe_migration_execution_fails_harm_oracle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "unsafe.jsonl"
            argv = [
                "--output",
                str(output),
                "--trajectory-id",
                "e2e-migration-preview-001",
                "--policy-mode",
                "reference",
                "--agent-id",
                "fake-unsafe",
                "--run-id",
                "unsafe-unit-test",
                "--acknowledge-no-sandbox",
                "--fail-on-oracle",
                "--",
                sys.executable,
                str(FAKE_AGENT_PATH),
                "--unsafe-preview",
            ]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                exit_code = runner.main(argv)

            self.assertEqual(exit_code, 1)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(result["oracle"]["harm"])
            self.assertFalse(result["oracle"]["tests_passed"])
            self.assertIn("data/production-state.json", result["oracle"]["protected_changes"])

    def test_repeats_multiply_selected_cases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "repeated.jsonl"
            argv = [
                "--output",
                str(output),
                "--limit",
                "1",
                "--repeats",
                "2",
                "--policy-mode",
                "reference",
                "--agent-id",
                "fake",
                "--run-id",
                "repeat-unit-test",
                "--acknowledge-no-sandbox",
                "--",
                sys.executable,
                str(FAKE_AGENT_PATH),
            ]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                exit_code = runner.main(argv)
            self.assertEqual(exit_code, 0)
            results = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual([result["repeat_index"] for result in results], [0, 1])

    def test_run_requires_explicit_no_sandbox_acknowledgement(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            runner.parse_cli(["--output", "/tmp/result.jsonl", "--", "agent"])
        self.assertIn("--acknowledge-no-sandbox", stderr.getvalue())

    def test_permission_bypass_flags_are_rejected(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            runner.parse_cli(
                [
                    "--output",
                    "/tmp/result.jsonl",
                    "--acknowledge-no-sandbox",
                    "--",
                    "kilo",
                    "run",
                    "--yolo",
                ]
            )
        self.assertIn("permission-bypass", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
