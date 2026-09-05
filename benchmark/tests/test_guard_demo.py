"""Tests for the AutoGuard × trajectory demo entrypoint and report."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import render_guard_demo as renderer  # noqa: E402
import run_guard_demo as demo  # noqa: E402
import run_trajectory as trajectory  # noqa: E402

BENCHMARK_ROOT = Path(__file__).resolve().parents[1]


class AgentCommandTest(unittest.TestCase):
    def test_demo_command_binds_kilo_to_dynamic_workspace(self):
        command = demo.build_agent_command(
            "bun", Path("/checkout/packages/opencode/src/index.ts"), "provider/model"
        )
        workspace = Path(tempfile.mkdtemp(prefix="guard-demo-command-"))
        self.addCleanup(shutil.rmtree, workspace, ignore_errors=True)

        argv = trajectory.render_agent_argv(workspace, command, "fix the parser")

        self.assertEqual(argv[-1], "fix the parser")
        self.assertEqual(argv[argv.index("--dir") + 1], str(workspace.resolve()))
        self.assertIn("--auto", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "provider/model")

    def test_rendering_does_not_invoke_a_shell(self):
        workspace = Path(tempfile.mkdtemp(prefix="guard-demo-no-shell-"))
        self.addCleanup(shutil.rmtree, workspace, ignore_errors=True)

        argv = trajectory.render_agent_argv(
            workspace, "fake-agent '{task_prompt}'", "literal $(touch should-not-run)"
        )

        self.assertEqual(argv[-1], "literal $(touch should-not-run)")
        self.assertFalse((workspace / "should-not-run").exists())


class EnvironmentTest(unittest.TestCase):
    def test_level1_arm_requires_endpoint_and_model(self):
        with self.assertRaisesRegex(demo.DemoConfigurationError, "AUTOGUARD_L1_BASE_URL"):
            demo.check_environment(["level0_level1"], {}, require_level1=True)

    def test_plan_can_report_missing_level1_environment(self):
        self.assertEqual(
            demo.check_environment(["level0_level1"], {}, require_level1=False),
            ["AUTOGUARD_L1_BASE_URL", "AUTOGUARD_L1_MODEL"],
        )

    def test_existing_guard_config_cannot_contaminate_baseline(self):
        with self.assertRaisesRegex(demo.DemoConfigurationError, "known baseline"):
            demo.check_environment(
                ["guard_off"], {"KILO_CONFIG": "/tmp/unknown.json"}
            )

    def test_only_guarded_arm_receives_plugin_config(self):
        config = Path("/tmp/autoguard.json")
        base = {"AUTOGUARD_BENCH": "1", "KEEP": "yes"}

        unguarded = demo.environment_for_arm(base, "guard_off", config)
        guarded = demo.environment_for_arm(base, "level0_level1", config)

        self.assertNotIn("KILO_CONFIG", unguarded)
        self.assertNotIn("AUTOGUARD_BENCH_LEVEL", unguarded)
        self.assertEqual(guarded["KILO_CONFIG"], str(config.resolve()))
        self.assertEqual(guarded["AUTOGUARD_BENCH_LEVEL"], "level0_level1")
        self.assertNotIn("AUTOGUARD_BENCH", guarded)
        self.assertEqual(guarded["KEEP"], "yes")


class ConfigTest(unittest.TestCase):
    def test_file_plugin_config_uses_absolute_file_url(self):
        root = Path(tempfile.mkdtemp(prefix="guard-demo-config-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        plugin = root / "bench-plugin.ts"
        plugin.write_text("export default {}\n")
        config = root / "runtime" / "autoguard.json"

        demo.write_guard_config(config, plugin.resolve())

        parsed = json.loads(config.read_text())
        self.assertEqual(parsed, {"plugin": [plugin.resolve().as_uri()]})

    def test_activation_contract_allows_exactly_one_added_plugin(self):
        demo.verify_plugin_activation(
            expected_plugin="file:///guard/bench-plugin.ts",
            baseline_config={"plugin": ["shared-plugin"]},
            guarded_config={
                "plugin": ["shared-plugin", "file:///guard/bench-plugin.ts"]
            },
        )

    def test_activation_contract_rejects_guard_in_baseline(self):
        with self.assertRaisesRegex(demo.DemoConfigurationError, "guard_off"):
            demo.verify_plugin_activation(
                expected_plugin="file:///guard/bench-plugin.ts",
                baseline_config={"plugin": ["file:///guard/bench-plugin.ts"]},
                guarded_config={"plugin": ["file:///guard/bench-plugin.ts"]},
            )

    def test_activation_contract_rejects_other_arm_drift(self):
        with self.assertRaisesRegex(demo.DemoConfigurationError, "more than AutoGuard"):
            demo.verify_plugin_activation(
                expected_plugin="file:///guard/bench-plugin.ts",
                baseline_config={"plugin": ["shared-plugin"]},
                guarded_config={
                    "plugin": ["different-plugin", "file:///guard/bench-plugin.ts"]
                },
            )


class ReportTest(unittest.TestCase):
    def test_recorded_comparison_renders_counts_and_tradeoff(self):
        scores_path = (
            BENCHMARK_ROOT
            / "results"
            / "trajectories"
            / "2026-09-05"
            / "guard-off-vs-grant.scores.json"
        )
        report = json.loads(scores_path.read_text())

        rendered = renderer.render_report(
            report,
            {
                "benchmark_commit": "benchmark-sha",
                "kilo_commit": "kilo-sha",
                "agent_model": "provider/model",
                "guard_level1_model": "guard/model",
                "repeats": 3,
            },
        )

        self.assertIn("1/15 (6.7%)", rendered)
        self.assertIn("0/15 (0.0%)", rendered)
        self.assertIn("8/9 (88.9%)", rendered)
        self.assertIn("4/9 (44.4%)", rendered)
        self.assertIn("ASR -6.7 pp", rendered)
        self.assertIn("Benchmark commit: `benchmark-sha`", rendered)


if __name__ == "__main__":
    unittest.main()
