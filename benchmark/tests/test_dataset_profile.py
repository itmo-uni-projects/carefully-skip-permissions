#!/usr/bin/env python3
"""Regression tests for the time-boxed 6 x 2 x 3 hackathon dataset profile."""

from __future__ import annotations

import json
import unittest
from collections import Counter, defaultdict
from pathlib import Path


BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
CASES_PATH = BENCHMARK_ROOT / "datasets" / "action-policy" / "dev" / "cases.jsonl"
LABELS_PATH = BENCHMARK_ROOT / "datasets" / "action-policy" / "dev" / "labels.jsonl"

EXPECTED_FAMILIES = {
    "filesystem_delete",
    "git_push",
    "filesystem_permissions",
    "network_upload",
    "code_modification",
    "dependency_execution",
}
EXPECTED_DECISIONS = {"ALLOW", "ASK", "DENY"}


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class DatasetProfileTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_jsonl(CASES_PATH)
        cls.labels = load_jsonl(LABELS_PATH)
        cls.labels_by_id = {label["case_id"]: label for label in cls.labels}

    def test_balanced_6_by_2_by_3_profile(self) -> None:
        self.assertEqual(len(self.cases), 36)
        self.assertEqual(len(self.labels), 36)
        self.assertEqual({case["family"] for case in self.cases}, EXPECTED_FAMILIES)

        groups_by_family: dict[str, set[str]] = defaultdict(set)
        cases_per_family: Counter[str] = Counter()
        decisions_per_family: dict[str, Counter[str]] = defaultdict(Counter)
        members_by_group: dict[str, list[str]] = defaultdict(list)

        for case in self.cases:
            family = case["family"]
            case_id = case["case_id"]
            groups_by_family[family].add(case["group_id"])
            cases_per_family[family] += 1
            decisions_per_family[family][self.labels_by_id[case_id]["expected_decision"]] += 1
            members_by_group[case["group_id"]].append(case_id)

        self.assertEqual(len(members_by_group), 12)
        for family in EXPECTED_FAMILIES:
            self.assertEqual(len(groups_by_family[family]), 2, family)
            self.assertEqual(cases_per_family[family], 6, family)
            self.assertEqual(decisions_per_family[family], Counter({d: 2 for d in EXPECTED_DECISIONS}), family)

        for group_id, case_ids in members_by_group.items():
            self.assertEqual(len(case_ids), 3, group_id)
            decisions = {self.labels_by_id[case_id]["expected_decision"] for case_id in case_ids}
            self.assertEqual(decisions, EXPECTED_DECISIONS, group_id)

    def test_case_label_join_is_exact(self) -> None:
        self.assertEqual(
            {case["case_id"] for case in self.cases},
            {label["case_id"] for label in self.labels},
        )

    def test_authority_sets_are_disjoint(self) -> None:
        for case in self.cases:
            authority = case["input"]["authority"]
            required = set(authority["required"])
            implicit = set(authority["implicit"])
            sensitive = set(authority["sensitive"])
            self.assertFalse(required & implicit, case["case_id"])
            self.assertFalse(required & sensitive, case["case_id"])
            self.assertFalse(implicit & sensitive, case["case_id"])

    def test_risk_is_not_a_decision_proxy(self) -> None:
        self.assertTrue(
            any(label["expected_decision"] == "ALLOW" and label["risk_level"] in {"high", "critical"} for label in self.labels)
        )
        self.assertTrue(
            any(label["expected_decision"] == "DENY" and label["risk_level"] in {"low", "medium"} for label in self.labels)
        )

    def test_model_proposed_cases_remain_draft(self) -> None:
        for case in self.cases:
            if case["provenance"]["source"] == "llm_draft":
                self.assertEqual(self.labels_by_id[case["case_id"]]["review_status"], "draft")


if __name__ == "__main__":
    unittest.main()
