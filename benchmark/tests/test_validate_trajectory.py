"""Tests for the trajectory dataset validator.

Each test asserts that one specific way of silently breaking the benchmark is
caught. A validator that passes a broken dataset is worse than no validator:
a payload that never gets planted reads as a perfect ASR of zero.
"""

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import validate_trajectory as vt  # noqa: E402

SCENARIOS = Path(__file__).resolve().parents[1] / "datasets" / "trajectories" / "dev" / "scenarios.jsonl"


def load() -> list[dict]:
    return [json.loads(line) for line in SCENARIOS.read_text().splitlines() if line.strip()]


def control(scenarios: list[dict]) -> dict:
    return next(s for s in scenarios if not s["injection"]["present"])


def injected(scenarios: list[dict]) -> dict:
    return next(s for s in scenarios if s["injection"]["present"])


class ShippedDatasetTest(unittest.TestCase):
    def test_dev_split_passes_every_check(self):
        report = vt.Report()
        scenarios = load()
        vt.check_ids(scenarios, report)
        vt.check_groups(scenarios, report)
        for scenario in scenarios:
            vt.check_injection_consistency(scenario, report)
            vt.check_fixture_and_payload(scenario, report)
            vt.check_oracles(scenario, report)
        self.assertEqual(report.errors, [])

    def test_every_group_has_exactly_one_control(self):
        groups: dict[str, int] = {}
        for scenario in load():
            if not scenario["injection"]["present"]:
                groups[scenario["group_id"]] = groups.get(scenario["group_id"], 0) + 1
        self.assertTrue(groups)
        self.assertTrue(all(count == 1 for count in groups.values()), groups)


class IdTest(unittest.TestCase):
    def test_reserved_outcome_word_in_an_id_is_rejected(self):
        scenarios = load()
        scenarios[0] = {**scenarios[0], "scenario_id": "traj-parse-deny-case"}
        report = vt.Report()
        vt.check_ids(scenarios, report)
        self.assertTrue(any("reserved outcome word" in e for e in report.errors))

    def test_duplicate_scenario_id_is_rejected(self):
        scenarios = load()
        report = vt.Report()
        vt.check_ids([scenarios[0], copy.deepcopy(scenarios[0])], report)
        self.assertTrue(any("duplicate scenario_id" in e for e in report.errors))


class GroupTest(unittest.TestCase):
    def test_two_controls_in_one_group_is_rejected(self):
        first = control(load())
        second = {**copy.deepcopy(first), "scenario_id": "traj-second-control"}
        report = vt.Report()
        vt.check_groups([first, second], report)
        self.assertTrue(any("exactly one benign control" in e for e in report.errors))

    def test_group_with_no_injected_member_is_rejected(self):
        report = vt.Report()
        vt.check_groups([control(load())], report)
        self.assertTrue(any("at least one injected member" in e for e in report.errors))

    def test_members_must_share_the_task_prompt(self):
        scenarios = load()
        group_id = injected(scenarios)["group_id"]
        members = [copy.deepcopy(s) for s in scenarios if s["group_id"] == group_id]
        members[0]["task_prompt"] = "something else entirely"
        report = vt.Report()
        vt.check_groups(members, report)
        self.assertTrue(any("must share task_prompt" in e for e in report.errors))


class InjectionConsistencyTest(unittest.TestCase):
    def test_control_carrying_a_payload_is_rejected(self):
        scenario = copy.deepcopy(control(load()))
        scenario["injection"]["payload_id"] = "exfil-env-v1"
        report = vt.Report()
        vt.check_injection_consistency(scenario, report)
        self.assertTrue(any("benign control declares a payload_id" in e for e in report.errors))

    def test_injected_scenario_without_a_target_harm_is_rejected(self):
        scenario = copy.deepcopy(injected(load()))
        scenario["injection"]["target_harm"] = "none"
        report = vt.Report()
        vt.check_injection_consistency(scenario, report)
        self.assertTrue(any("target_harm 'none'" in e for e in report.errors))


class FixturePayloadTest(unittest.TestCase):
    def test_channel_the_fixture_does_not_declare_is_rejected(self):
        scenario = copy.deepcopy(injected(load()))
        scenario["injection"]["channel"] = "tool_result"
        report = vt.Report()
        vt.check_fixture_and_payload(scenario, report)
        self.assertTrue(any("declares no slot for channel" in e for e in report.errors))

    def test_missing_payload_file_is_rejected(self):
        scenario = copy.deepcopy(injected(load()))
        scenario["injection"]["payload_id"] = "payload-that-was-never-written"
        report = vt.Report()
        vt.check_fixture_and_payload(scenario, report)
        self.assertTrue(any("payload missing" in e for e in report.errors))

    def test_sink_payload_without_a_declared_sink_is_rejected(self):
        # The failure this catches is silent: planting raises at run time and
        # the scenario would otherwise look like a scenario nobody ever won.
        scenarios = load()
        scenario = copy.deepcopy(
            next(s for s in scenarios if s["injection"]["payload_id"] == "exfil-env-v1")
        )
        scenario["oracles"]["harm"]["canary_sink"] = None
        report = vt.Report()
        vt.check_fixture_and_payload(scenario, report)
        self.assertTrue(any("canary_sink: null" in e for e in report.errors))


class OracleConfigTest(unittest.TestCase):
    def test_harm_oracle_observing_nothing_is_rejected(self):
        scenario = copy.deepcopy(control(load()))
        scenario["oracles"]["harm"] = {"canary_files": [], "canary_sink": None, "protected_paths": []}
        report = vt.Report()
        vt.check_oracles(scenario, report)
        self.assertTrue(any("observes nothing" in e for e in report.errors))

    def test_no_permitted_change_at_all_is_rejected(self):
        scenario = copy.deepcopy(control(load()))
        scenario["oracles"]["collateral"] = {
            "allowed_change_globs": [],
            "allowed_delete_globs": [],
            "ignore_globs": [],
        }
        report = vt.Report()
        vt.check_oracles(scenario, report)
        self.assertTrue(any("no way to solve the task" in e for e in report.errors))

    def test_a_glob_that_is_both_allowed_and_ignored_is_rejected(self):
        scenario = copy.deepcopy(control(load()))
        scenario["oracles"]["collateral"] = {
            "allowed_change_globs": ["src/parser.py"],
            "allowed_delete_globs": [],
            "ignore_globs": ["src/parser.py"],
        }
        report = vt.Report()
        vt.check_oracles(scenario, report)
        self.assertTrue(any("both allowed_change_globs and ignore_globs" in e for e in report.errors))


if __name__ == "__main__":
    unittest.main()
