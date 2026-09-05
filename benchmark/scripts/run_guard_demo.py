#!/usr/bin/env python3
"""Run the same Kilo checkout with AutoGuard off and on, then render metrics.

This is the demo-day entrypoint for the injection trajectory suite. It does
not implement another runner or another scorer: it pins the Guard checkout,
constructs the two configurations, delegates execution to run_trajectory.py,
validates the records, scores both arms together and renders a compact
Markdown table.

The Kilo process is deliberately restarted for every trial by the underlying
runner. The two arms use the same source checkout and model; the only intended
difference is whether the benchmark file plugin is present in KILO_CONFIG.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
BENCHMARK_ROOT = SCRIPT_DIR.parent
REPOSITORY_ROOT = BENCHMARK_ROOT.parent
DEFAULT_LOCK = BENCHMARK_ROOT / "guard-demo.lock.json"
DEFAULT_SCENARIOS = BENCHMARK_ROOT / "datasets" / "trajectories" / "dev" / "scenarios.jsonl"

RUNNER = SCRIPT_DIR / "run_trajectory.py"
VALIDATOR = SCRIPT_DIR / "validate_trajectory.py"
SCORER = SCRIPT_DIR / "score_trajectory.py"
RENDERER = SCRIPT_DIR / "render_guard_demo.py"

ARMS = ("guard_off", "level0_only", "level0_level1")
DEFAULT_ARMS = ("guard_off", "level0_level1")
REQUIRED_LEVEL1_ENV = ("AUTOGUARD_L1_BASE_URL", "AUTOGUARD_L1_MODEL")


class DemoConfigurationError(ValueError):
    """The requested comparison would be unsafe or not reproducible."""


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise DemoConfigurationError(f"git {' '.join(args)} failed for {repo}: {detail}")
    return completed.stdout.strip()


def load_lock(path: Path) -> dict[str, Any]:
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DemoConfigurationError(f"cannot read Guard lock {path}: {exc}") from exc
    required = ("demo_commit", "entrypoint", "plugin", "pull_requests")
    missing = [field for field in required if not lock.get(field)]
    if missing:
        raise DemoConfigurationError(f"Guard lock {path} is missing {missing}")
    return lock


def inspect_kilo_checkout(
    repo: Path,
    lock: dict[str, Any],
    *,
    allow_dirty: bool,
    allow_unpinned: bool,
) -> dict[str, Any]:
    repo = repo.resolve()
    if not repo.is_dir():
        raise DemoConfigurationError(f"Kilo checkout does not exist: {repo}")

    commit = _git(repo, "rev-parse", "HEAD")
    dirty = bool(_git(repo, "status", "--porcelain"))
    if dirty and not allow_dirty:
        raise DemoConfigurationError(
            "Kilo checkout has uncommitted changes; commit/stash them or pass --allow-dirty-kilo"
        )
    if commit != lock["demo_commit"] and not allow_unpinned:
        raise DemoConfigurationError(
            f"Kilo is at {commit}, but guard-demo.lock.json pins {lock['demo_commit']}; "
            "checkout the pinned PR head or pass --allow-unpinned-kilo"
        )

    entrypoint = repo / lock["entrypoint"]
    plugin = repo / lock["plugin"]
    for label, path in (("Kilo entrypoint", entrypoint), ("AutoGuard benchmark plugin", plugin)):
        if not path.is_file():
            raise DemoConfigurationError(f"{label} is missing: {path}")

    return {
        "root": repo,
        "commit": commit,
        "dirty": dirty,
        "branch": _git(repo, "branch", "--show-current") or "detached",
        "entrypoint": entrypoint.resolve(),
        "plugin": plugin.resolve(),
    }


def build_agent_command(bun: str, entrypoint: Path, agent_model: str) -> str:
    """Return an argv template consumed by run_trajectory.py.

    Both placeholders survive shlex.join as single argv elements and are
    replaced by the runner after it creates each disposable workspace.
    """
    return shlex.join(
        [
            bun,
            "--conditions=node",
            str(entrypoint),
            "run",
            "--auto",
            "--format",
            "json",
            "--model",
            agent_model,
            "--dir",
            "{workspace}",
            "{task_prompt}",
        ]
    )


def write_guard_config(path: Path, plugin: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"plugin": [plugin.as_uri()]}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def check_environment(
    arms: list[str], environment: dict[str, str], *, require_level1: bool = True
) -> list[str]:
    if "guard_off" in arms and environment.get("KILO_CONFIG"):
        raise DemoConfigurationError(
            "KILO_CONFIG is already set, so guard_off would not be a known baseline; "
            "run with `env -u KILO_CONFIG ...`"
        )
    inline = environment.get("KILO_CONFIG_CONTENT", "").lower()
    if "guard_off" in arms and ("autoguard" in inline or "bench-plugin" in inline):
        raise DemoConfigurationError(
            "KILO_CONFIG_CONTENT already activates AutoGuard, so guard_off would be contaminated"
        )
    missing: list[str] = []
    if "level0_level1" in arms:
        missing = [name for name in REQUIRED_LEVEL1_ENV if not environment.get(name)]
        if missing and require_level1:
            raise DemoConfigurationError(
                "level0_level1 requires " + ", ".join(missing) + " in the environment"
            )
    return missing


def environment_for_arm(
    base: dict[str, str], arm: str, guard_config: Path
) -> dict[str, str]:
    env = dict(base)
    env["KILO_CLIENT"] = "cli"
    # AUTOGUARD_BENCH belonged to an older internal-registration prototype.
    # Current PR #2 is activated only through the explicit file plugin.
    env.pop("AUTOGUARD_BENCH", None)
    if arm == "guard_off":
        env.pop("KILO_CONFIG", None)
        env.pop("AUTOGUARD_BENCH_LEVEL", None)
    else:
        env["KILO_CONFIG"] = str(guard_config.resolve())
        env["AUTOGUARD_BENCH_LEVEL"] = arm
    return env


def plugin_specs(config: dict[str, Any]) -> set[str]:
    specs: set[str] = set()
    for plugin in config.get("plugin") or []:
        value = plugin[0] if isinstance(plugin, list) and plugin else plugin
        if isinstance(value, str):
            specs.add(value)
    return specs


def verify_plugin_activation(
    *,
    expected_plugin: str,
    baseline_config: dict[str, Any] | None,
    guarded_config: dict[str, Any] | None,
) -> None:
    baseline = plugin_specs(baseline_config or {})
    guarded = plugin_specs(guarded_config or {})
    if baseline_config is not None and expected_plugin in baseline:
        raise DemoConfigurationError("AutoGuard plugin is active in guard_off preflight")
    if guarded_config is not None and expected_plugin not in guarded:
        raise DemoConfigurationError("AutoGuard plugin is absent from guarded preflight")
    if baseline_config is not None and guarded_config is not None:
        expected_guarded = baseline | {expected_plugin}
        if guarded != expected_guarded:
            raise DemoConfigurationError(
                "plugin sets differ by more than AutoGuard: "
                f"baseline={sorted(baseline)}, guarded={sorted(guarded)}"
            )


def resolve_kilo_config(
    *,
    bun: str,
    entrypoint: Path,
    cwd: Path,
    environment: dict[str, str],
    timeout: int,
) -> dict[str, Any]:
    command = [bun, "--conditions=node", str(entrypoint), "debug", "config"]
    print("+ " + shlex.join(command) + "  # configuration preflight", file=sys.stderr)
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise DemoConfigurationError(
            f"Kilo configuration preflight exceeded {timeout}s"
        ) from exc
    except OSError as exc:
        raise DemoConfigurationError(f"cannot launch Kilo configuration preflight: {exc}") from exc
    if completed.returncode:
        detail = completed.stderr.strip()[-2000:]
        raise DemoConfigurationError(
            f"Kilo configuration preflight exited with {completed.returncode}: {detail}"
        )
    try:
        resolved = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DemoConfigurationError(
            "Kilo configuration preflight did not return JSON; rerun the printed command manually"
        ) from exc
    if not isinstance(resolved, dict):
        raise DemoConfigurationError("Kilo configuration preflight returned a non-object")
    return resolved


def runner_command(
    *,
    arm: str,
    output: Path,
    scenarios: Path,
    repeats: int,
    agent_command: str,
    agent_timeout: int,
    agent_model: str,
    guard_level1_model: str | None,
    kilo_commit: str,
    scenario_ids: list[str] | None,
    limit: int | None,
    temperature: float,
    seed: int | None,
) -> list[str]:
    command = [
        sys.executable,
        str(RUNNER),
        "--scenarios",
        str(scenarios),
        "--arm",
        arm,
        "--repeats",
        str(repeats),
        "--agent-cmd",
        agent_command,
        "--agent-timeout",
        str(agent_timeout),
        "--agent-model",
        agent_model,
        "--kilo-commit",
        kilo_commit,
        "--guard-commit",
        kilo_commit if arm != "guard_off" else "inactive@" + kilo_commit,
        "--temperature",
        str(temperature),
        "--output",
        str(output),
    ]
    if arm == "level0_level1" and guard_level1_model:
        command.extend(["--guard-level1-model", guard_level1_model])
    if limit is not None:
        command.extend(["--limit", str(limit)])
    if seed is not None:
        command.extend(["--seed", str(seed)])
    for scenario_id in scenario_ids or []:
        command.extend(["--scenario-id", scenario_id])
    return command


def _run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+ " + shlex.join(command), file=sys.stderr)
    completed = subprocess.run(command, cwd=BENCHMARK_ROOT, env=env, check=False)
    if completed.returncode:
        raise DemoConfigurationError(
            f"command exited with {completed.returncode}: {shlex.join(command)}"
        )


def _output_root(value: Path | None) -> Path:
    if value:
        return value.resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (BENCHMARK_ROOT / "results" / "raw" / f"guard-demo-{stamp}").resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kilo-repo", type=Path, required=True)
    parser.add_argument("--agent-model", required=True)
    parser.add_argument("--arm", action="append", choices=ARMS, dest="arms")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--scenario-id", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--agent-timeout", type=int, default=900)
    parser.add_argument("--config-preflight-timeout", type=int, default=900)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--bun", default="bun")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--allow-unpinned-kilo", action="store_true")
    parser.add_argument("--allow-dirty-kilo", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument(
        "--acknowledge-no-os-sandbox",
        action="store_true",
        help="required for a live run; temporary git workspaces are not process containment",
    )
    args = parser.parse_args()

    arms = list(dict.fromkeys(args.arms or DEFAULT_ARMS))
    if args.repeats < 1:
        print("--repeats must be at least 1", file=sys.stderr)
        return 2
    if args.limit is not None and args.limit < 1:
        print("--limit must be at least 1", file=sys.stderr)
        return 2
    if not args.plan_only and not args.acknowledge_no_os_sandbox:
        print(
            "live agent execution requires --acknowledge-no-os-sandbox; use a disposable "
            "container/VM without secrets",
            file=sys.stderr,
        )
        return 2

    try:
        lock = load_lock(args.lock)
        checkout = inspect_kilo_checkout(
            args.kilo_repo,
            lock,
            allow_dirty=args.allow_dirty_kilo,
            allow_unpinned=args.allow_unpinned_kilo,
        )
        missing_level1_env = check_environment(
            arms, dict(os.environ), require_level1=not args.plan_only
        )
        if shutil.which(args.bun) is None and not args.plan_only:
            raise DemoConfigurationError(f"cannot find {args.bun!r} on PATH")

        output_root = _output_root(args.output_dir)
        guard_config = output_root / "runtime" / "autoguard.json"
        benchmark_commit = _git(REPOSITORY_ROOT, "rev-parse", "HEAD")
        benchmark_dirty = bool(_git(REPOSITORY_ROOT, "status", "--porcelain"))
        agent_command = build_agent_command(args.bun, checkout["entrypoint"], args.agent_model)
        manifest = {
            "schema_version": "0.1",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "benchmark_commit": benchmark_commit + ("+dirty" if benchmark_dirty else ""),
            "kilo_repository": lock.get("repository"),
            "kilo_commit": checkout["commit"],
            "kilo_branch": checkout["branch"],
            "kilo_dirty": checkout["dirty"],
            "guard_pull_requests": lock["pull_requests"],
            "agent_model": args.agent_model,
            "guard_level1_model": os.environ.get("AUTOGUARD_L1_MODEL"),
            "arms": arms,
            "repeats": args.repeats,
            "scenario_ids": args.scenario_id,
            "limit": args.limit,
            "agent_timeout_seconds": args.agent_timeout,
            "agent_command": agent_command,
            "missing_level1_environment": missing_level1_env,
        }

        print(json.dumps(manifest, indent=2, ensure_ascii=False))
        if args.plan_only:
            print(
                "plan-only: no config, model request, workspace or result was created",
                file=sys.stderr,
            )
            return 0

        output_root.mkdir(parents=True, exist_ok=False)
        write_guard_config(guard_config, checkout["plugin"])
        manifest_path = output_root / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        preflight_workspace = output_root / "runtime" / "preflight-workspace"
        preflight_workspace.mkdir(parents=True)
        baseline_config = (
            resolve_kilo_config(
                bun=args.bun,
                entrypoint=checkout["entrypoint"],
                cwd=preflight_workspace,
                environment=environment_for_arm(dict(os.environ), "guard_off", guard_config),
                timeout=args.config_preflight_timeout,
            )
            if "guard_off" in arms
            else None
        )
        guarded_arm = next((arm for arm in arms if arm != "guard_off"), None)
        guarded_config = (
            resolve_kilo_config(
                bun=args.bun,
                entrypoint=checkout["entrypoint"],
                cwd=preflight_workspace,
                environment=environment_for_arm(dict(os.environ), guarded_arm, guard_config),
                timeout=args.config_preflight_timeout,
            )
            if guarded_arm
            else None
        )
        verify_plugin_activation(
            expected_plugin=checkout["plugin"].as_uri(),
            baseline_config=baseline_config,
            guarded_config=guarded_config,
        )
        manifest["plugin_activation_preflight"] = {
            "guard_off_contains_guard": False if baseline_config is not None else None,
            "guarded_contains_guard": True if guarded_config is not None else None,
            "status": "passed",
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        _run([sys.executable, str(VALIDATOR), "--scenarios", str(args.scenarios)])
        run_paths: list[Path] = []
        for arm in arms:
            output = output_root / f"{arm}.jsonl"
            run_paths.append(output)
            command = runner_command(
                arm=arm,
                output=output,
                scenarios=args.scenarios,
                repeats=args.repeats,
                agent_command=agent_command,
                agent_timeout=args.agent_timeout,
                agent_model=args.agent_model,
                guard_level1_model=os.environ.get("AUTOGUARD_L1_MODEL"),
                kilo_commit=checkout["commit"],
                scenario_ids=args.scenario_id,
                limit=args.limit,
                temperature=args.temperature,
                seed=args.seed,
            )
            _run(command, env=environment_for_arm(dict(os.environ), arm, guard_config))
            _run(
                [
                    sys.executable,
                    str(VALIDATOR),
                    "--scenarios",
                    str(args.scenarios),
                    "--runs",
                    str(output),
                ]
            )

        scores_path = output_root / "scores.json"
        score_command = [sys.executable, str(SCORER)]
        for path in run_paths:
            score_command.extend(["--runs", str(path)])
        score_command.extend(
            ["--scenarios", str(args.scenarios), "--output", str(scores_path)]
        )
        _run(score_command)

        summary_path = output_root / "summary.md"
        _run(
            [
                sys.executable,
                str(RENDERER),
                "--scores",
                str(scores_path),
                "--manifest",
                str(manifest_path),
                "--output",
                str(summary_path),
            ]
        )
        print(f"\nDemo report: {summary_path}")
        return 0
    except DemoConfigurationError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
