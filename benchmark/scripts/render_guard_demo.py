#!/usr/bin/env python3
"""Render trajectory score JSON as a compact demo-ready Markdown report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _percentage_points(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.1f} pp"


def _count_rate(numerator: int, denominator: int, rate: float | None) -> str:
    return f"{numerator}/{denominator} ({_percent(rate)})"


def _milliseconds(summary: dict[str, Any] | None) -> str:
    if not summary:
        return "n/a"
    return f"{summary['median_ms']:,.0f} ms"


def _cell(scores: dict[str, Any], metric: str) -> str:
    if metric == "runs":
        return f"{scores['scored_runs']}/{scores['runs']} scored"
    if metric == "asr":
        value = scores["asr"]
        return _count_rate(value["harmed"], value["injected_runs"], value["micro_rate"])
    if metric == "benign_utility":
        value = scores["utility"]["benign"]
        return _count_rate(value["solved"], value["runs"], value["rate"])
    if metric == "injected_utility":
        value = scores["utility"]["injected"]
        return _count_rate(value["solved"], value["runs"], value["rate"])
    if metric == "benign_harm":
        value = scores["harm_without_attacker"]
        return _count_rate(value["harmed"], value["benign_runs"], value["rate"])
    if metric == "collateral":
        value = scores["collateral"]
        return _count_rate(
            value["runs_with_unexpected_changes"], scores["scored_runs"], value["rate"]
        )
    if metric == "asks":
        return str(scores["friction"]["ask_total"])
    if metric == "evaluated_actions":
        return str(scores["friction"].get("evaluated_actions_total", 0))
    if metric == "denies":
        return str(scores["friction"]["deny_total"])
    if metric == "fail_closed":
        return str(scores["friction"]["fail_closed_total"])
    if metric == "recovery":
        value = scores["recovery"]
        return _count_rate(value["solved_anyway"], value["runs_with_a_denial"], value["rate"])
    if metric == "run_latency":
        return _milliseconds(scores["latency"]["run_duration"])
    if metric == "guard_latency":
        return _milliseconds(scores["latency"]["guard_total_per_run"])
    raise KeyError(metric)


METRICS = (
    ("Scored runs", "runs"),
    ("Attack success (injected)", "asr"),
    ("Utility (benign)", "benign_utility"),
    ("Utility (injected)", "injected_utility"),
    ("Harm without attacker", "benign_harm"),
    ("Collateral-change runs", "collateral"),
    ("Guard-evaluated actions", "evaluated_actions"),
    ("ASK decisions", "asks"),
    ("DENY decisions", "denies"),
    ("Classifier failures", "fail_closed"),
    ("Solved after DENY", "recovery"),
    ("Median run latency", "run_latency"),
    ("Median guard latency/run", "guard_latency"),
)


def render_report(report: dict[str, Any], manifest: dict[str, Any] | None = None) -> str:
    arms = list(report["by_arm"])
    if not arms:
        raise ValueError("score report has no arms")

    lines = [
        "# AutoGuard × trajectory benchmark",
        "",
        "> Rates include absolute counts. A small demo sweep is evidence that the pipeline works, "
        "not a statistically stable model ranking.",
        "",
    ]

    comparison = report.get("comparison_contract")
    if comparison:
        if comparison["comparable"]:
            lines.extend(
                [
                    "> Comparison contract: **passed** — arms use the same trials, agent, "
                    "Kilo commit and environment.",
                    "",
                ]
            )
        else:
            reasons = "; ".join(comparison["reasons"])
            lines.extend(
                [
                    f"> Comparison contract: **failed** — {reasons}. Treat arm deltas as "
                    "illustrative, not causal.",
                    "",
                ]
            )

    if manifest:
        lines.extend(
            [
                "## Run identity",
                "",
                f"- Benchmark commit: `{manifest.get('benchmark_commit', 'unknown')}`",
                f"- Kilo/Guard commit: `{manifest.get('kilo_commit', 'unknown')}`",
                f"- Agent model: `{manifest.get('agent_model', 'unknown')}`",
                f"- Level 1 model: `{manifest.get('guard_level1_model') or 'not configured'}`",
                f"- Repeats per scenario: `{manifest.get('repeats', 'unknown')}`",
                "",
            ]
        )

    lines.extend(
        [
            "## Headline metrics",
            "",
            "| Metric | " + " | ".join(f"`{arm}`" for arm in arms) + " |",
            "|---|" + "---:|" * len(arms),
        ]
    )
    for label, key in METRICS:
        lines.append(
            f"| {label} | "
            + " | ".join(_cell(report["by_arm"][arm], key) for arm in arms)
            + " |"
        )

    deltas = report.get("deltas_vs_guard_off", {})
    if deltas:
        lines.extend(["", "## Delta vs `guard_off`", ""])
        for arm, values in deltas.items():
            lines.append(
                f"- `{arm}`: ASR {_percentage_points(values['asr_micro_delta'])}; "
                f"benign utility {_percentage_points(values['benign_utility_delta'])}; "
                f"collateral rate {_percentage_points(values['collateral_rate_delta'])}."
            )

    lines.extend(
        [
            "",
            "## Reading the result",
            "",
            "- ASR is harm observed on injected scenarios; lower is better.",
            "- Utility is reported separately so a block-everything policy cannot look successful.",
            "- `Classifier failures` counts fail-closed errors, not deliberate `ASK` decisions.",
            "- Compare arms only when agent model, scenarios, repeats, Kilo commit and environment match.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = json.loads(args.scores.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8")) if args.manifest else None
    rendered = render_report(report, manifest)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
