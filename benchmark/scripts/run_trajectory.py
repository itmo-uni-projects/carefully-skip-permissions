#!/usr/bin/env python3
"""Run end-to-end trajectory scenarios and judge them with the three oracles.

For every (scenario, repeat) this script:

  1. copies the scenario's fixture into a fresh temporary workspace outside
     the repository (never into benchmark/, which must stay clean),
  2. plants the injection payload in the requested channel, substituting the
     decoy sink's real URL into the payload text,
  3. starts a loopback HTTP decoy that records every request it receives,
  4. takes a digest snapshot of the workspace,
  5. runs the agent as a fresh subprocess -- a fresh process every time is
     mandatory, because Kilo caches project settings at workspace load
     (issue #7247) and a reused process measures stale policy,
  6. takes a second snapshot and hands both to the oracles,
  7. appends one trajectory-run record to --output and deletes the workspace.

Each record is written and flushed immediately, so an interrupted sweep still
leaves usable results on disk (same contract as run_action_policy.py).

The agent command is a template so this script does not hard-depend on a Kilo
install: `--agent-cmd 'kilo run --auto {task_prompt}'` is the real
configuration -- `--auto` is not optional, because without it Kilo auto-rejects
every permission request and the agent does nothing, which would read as a
perfect ASR of zero -- and
any other command that edits the workspace can stand in for a smoke test.

`--dry-run` skips the agent entirely and still runs the oracles. That is the
fixture self-test: a fixture whose verification command already passes with no
agent makes every utility number meaningless, and dry-run is what catches it.

Usage:
    python3 scripts/run_trajectory.py --arm guard_off \
        --agent-model openrouter/openai/gpt-oss-120b \
        --repeats 3 --output results/raw/traj.jsonl

    python3 scripts/run_trajectory.py --dry-run --output /tmp/fixture-check.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from dataclasses import dataclass, asdict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trajectory_oracles import judge_all, snapshot_workspace  # noqa: E402
from review_manifest import source as source_manifest, dataset as dataset_manifest, require_review
from trajectory_events import read_events, actions_from_events, waiting  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
BENCHMARK_ROOT = SCRIPT_DIR.parent
FIXTURES_ROOT = BENCHMARK_ROOT / "fixtures"
PAYLOADS_ROOT = FIXTURES_ROOT / "payloads"
DEFAULT_SCENARIOS = BENCHMARK_ROOT / "datasets" / "trajectories" / "dev" / "scenarios.jsonl"

ARMS = ("guard_off", "level0_only", "level0_level1_legacy", "level0_level1")
STATUS_OK = "ok"
STATUS_AGENT_TIMEOUT = "agent_timeout"
STATUS_INFRA_ERROR = "infrastructure_error"

SINK_PLACEHOLDER = "{{SINK_URL}}"


class FixtureEscaped(RuntimeError):
    """The on-disk fixture changed while the agent was running."""


# --------------------------------------------------------------------------
# decoy sink
# --------------------------------------------------------------------------


class _SinkHandler(BaseHTTPRequestHandler):
    """Records anything that reaches it. Nothing in a synthetic fixture has a
    legitimate reason to contact this address, so one request is evidence."""

    def _record(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        # Read and discard the body: its size is evidence, its content is not
        # something this repository should ever store.
        if length:
            self.rfile.read(length)
        self.server.requests.append(  # type: ignore[attr-defined]
            {"method": self.command, "path": self.path, "body_bytes": length}
        )
        self.send_response(200)
        self.end_headers()

    do_GET = _record
    do_POST = _record
    do_PUT = _record

    def log_message(self, *args: Any) -> None:
        return  # keep the sweep's stderr readable


class DecoySink:
    """Loopback HTTP server that logs requests. Port 0 lets the OS pick a free
    port, so parallel runs never collide."""

    def __init__(self, bind_host: str) -> None:
        self.server = HTTPServer((bind_host, 0), _SinkHandler)
        self.server.requests = []  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def requests(self) -> list[dict[str, Any]]:
        return self.server.requests  # type: ignore[attr-defined]

    def __enter__(self) -> DecoySink:
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()


# --------------------------------------------------------------------------
# workspace preparation
# --------------------------------------------------------------------------


def prepare_workspace(
    scenario: dict[str, Any], sink_url: str | None, git_init: bool = True
) -> Path:
    """Copy the fixture to a temp dir and plant the injection, if any."""
    fixture_dir = FIXTURES_ROOT / scenario["fixture"]
    if not fixture_dir.is_dir():
        raise FileNotFoundError(f"fixture not found: {fixture_dir}")

    workspace = Path(tempfile.mkdtemp(prefix=f"traj-{scenario['scenario_id']}-"))
    shutil.copytree(fixture_dir, workspace, dirs_exist_ok=True)
    (workspace / "fixture.json").unlink(missing_ok=True)

    injection = scenario["injection"]
    if injection["present"]:
        plant_injection(workspace, fixture_dir, injection, sink_url)
    else:
        strip_markers(workspace, fixture_dir)

    if git_init:
        # Give the workspace a repository boundary of its own. Coding agents
        # locate "the project" by walking up for a git root; without one an
        # agent launched in a bare temp directory can decide the project is
        # somewhere else entirely and edit files outside the sandbox.
        # `.git/**` is ignored by the snapshotter, so this is invisible to the
        # oracles, and it is also what a real checkout looks like.
        subprocess.run(["git", "init", "-q"], cwd=workspace, check=False, capture_output=True)

    return workspace


def fixture_digest(fixture_dir: Path) -> str:
    """One digest over the entire on-disk fixture tree.

    Compared before and after every agent run. If the fixture the sweep is
    copying from changes while the sweep is running, the agent has escaped its
    temporary workspace and is editing the repository -- and every subsequent
    run of that scenario would start from silently different source. This has
    happened in practice: an agent whose working directory was set only via
    the child process's `cwd` resolved its project root elsewhere and wrote its
    fix into `benchmark/fixtures/` instead of the sandbox, which made a solved
    task look unsolved and left the fixture permanently "already fixed".
    """
    digest = hashlib.sha256()
    for path in sorted(fixture_dir.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        digest.update(path.relative_to(fixture_dir).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _slots(fixture_dir: Path) -> dict[str, dict[str, str]]:
    manifest = json.loads((fixture_dir / "fixture.json").read_text())
    return manifest["injection_slots"]


def plant_injection(
    workspace: Path,
    fixture_dir: Path,
    injection: dict[str, Any],
    sink_url: str | None,
) -> None:
    channel = injection["channel"]
    slots = _slots(fixture_dir)
    if channel not in slots:
        raise ValueError(
            f"fixture {fixture_dir.name} declares no slot for channel {channel!r}"
        )

    payload = (PAYLOADS_ROOT / f"{injection['payload_id']}.md").read_text()
    if SINK_PLACEHOLDER in payload:
        if sink_url is None:
            raise ValueError(
                f"payload {injection['payload_id']} needs a sink, but the "
                "scenario declares canary_sink: null"
            )
        payload = payload.replace(SINK_PLACEHOLDER, sink_url)

    slot = slots[channel]
    payload = apply_line_prefix(payload, slot.get("line_prefix"))

    target = workspace / slot["path"]
    text = target.read_text()
    if slot["marker"] not in text:
        raise ValueError(f"marker {slot['marker']!r} missing from {slot['path']}")
    target.write_text(text.replace(slot["marker"], payload))

    # Every other slot in the fixture is a leftover marker; clear it so the
    # benign and injected members differ in exactly one place.
    strip_markers(workspace, fixture_dir, keep=channel)


def apply_line_prefix(payload: str, line_prefix: str | None) -> str:
    """Prefix every payload line, e.g. with '# ' for a source-code slot.

    Without this a markdown payload dropped into a .py file is a syntax error,
    the fixture stops importing, its own tests fail for a reason that has
    nothing to do with the agent -- and the run reads as an attack defeated.
    """
    if not line_prefix:
        return payload
    return "\n".join(line_prefix + line if line else line_prefix.rstrip() for line in payload.splitlines())


def strip_markers(workspace: Path, fixture_dir: Path, keep: str | None = None) -> None:
    for channel, slot in _slots(fixture_dir).items():
        if channel == keep:
            continue
        target = workspace / slot["path"]
        if target.exists():
            text = target.read_text()
            if slot["marker"] in text:
                target.write_text(text.replace(slot["marker"], ""))


# --------------------------------------------------------------------------
# the agent
# --------------------------------------------------------------------------


@dataclass
class AgentOutcome:
    status: str
    duration_ms: int
    exit_code: int | None
    end_reason: str
    error: str | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    usage: dict[str, Any] | None = None
    protocol_status: str | None = None

    def __iter__(self):
        # Compatibility for callers of the old (status, duration) interface.
        yield self.status
        yield self.duration_ms


def private_environment() -> dict[str, str]:
    """Read literal assignments; never execute or echo a private env file."""
    source = Path(os.environ.get("AUTOGUARD_ENV_FILE", Path.home() / ".config/autoguard/models.env"))
    if not source.exists(): return {}
    values = {}
    for line in source.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line: continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip()
        if key.startswith("AUTOGUARD_") or key == "OPENROUTER_API_KEY":
            values[key] = value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'" else value
    return values


def run_agent(workspace: Path, agent_cmd: str, task_prompt: str, timeout_s: int,
              *, environment: dict[str, str] | None = None, diagnostics: Path | None = None,
              audit: Path | None = None, interaction: str = "autonomous") -> AgentOutcome:
    argv = [part.replace("{task_prompt}", task_prompt).replace("{workspace}", str(workspace.resolve())) for part in shlex.split(agent_cmd)]
    env = {**private_environment(), **os.environ, **(environment or {}), "PWD": str(workspace)}
    folder = diagnostics or Path(tempfile.mkdtemp(prefix="autoguard-diagnostics-"))
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    stdout, stderr = folder / "stdout.log", folder / "stderr.log"
    started = time.monotonic()
    status, reason, code, error = STATUS_OK, "process_exit", None, None
    proc = None
    pending_since = None
    try:
        with stdout.open("wb") as out, stderr.open("wb") as err:
            os.chmod(stdout, 0o600); os.chmod(stderr, 0o600)
            proc = subprocess.Popen(argv, cwd=workspace, env=env, stdout=out, stderr=err, start_new_session=True)
            while proc.poll() is None:
                events, _, _ = read_events(audit)
                if audit and waiting(events):
                    pending_since = pending_since or time.monotonic()
                    if interaction == "autonomous" or time.monotonic() - pending_since >= 2:
                        status, reason = "waiting_user", "pending_approval"
                        break
                else:
                    pending_since = None
                if time.monotonic() - started >= timeout_s:
                    status, reason = STATUS_AGENT_TIMEOUT, "timeout"
                    break
                time.sleep(0.05)
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait()
            code = proc.returncode
            if status == STATUS_OK and code != 0:
                status, reason = "agent_error", "nonzero_exit"
    except OSError as exc:
        status, reason, error = STATUS_INFRA_ERROR, "launch_error", f"{type(exc).__name__}: {exc}"
    finally:
        if proc is not None and proc.poll() is None:
            try: os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            proc.wait()
    usage, protocol = protocol_summary(stdout)
    if protocol == "empty_final_response" and status == STATUS_OK:
        reason = "empty_final_response"
    if protocol == "agent_protocol_error" and status == STATUS_OK:
        status, reason = "agent_error", "agent_protocol_error"
    return AgentOutcome(status, int((time.monotonic() - started) * 1000), code, reason, error, str(stdout), str(stderr), usage, protocol)


def protocol_summary(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists(): return None, None
    finishes, visible, error = [], False, False
    for line in path.read_text(errors="replace").splitlines():
        try: event = json.loads(line)
        except ValueError: continue
        if not isinstance(event, dict): continue
        kind = event.get("type")
        if kind == "step_start": visible = False
        if kind == "text" and event.get("part", {}).get("text", "").strip(): visible = True
        if kind == "error": error = True
        if kind == "step_finish": finishes.append(event.get("part", {}))
    if not finishes: return None, "agent_protocol_error" if error else None
    usage = {"agent_cost_usd": sum(item.get("cost", 0) for item in finishes),
             "tokens": {key: sum(item.get("tokens", {}).get(key, 0) or 0 for item in finishes) for key in ("input", "output", "reasoning")},
             "l1_cost_usd": None}
    state = "agent_protocol_error" if error else "empty_final_response" if finishes[-1].get("reason") == "stop" and not visible else "completed" if finishes[-1].get("reason") == "stop" else "no_final_stop"
    return usage, state


def load_guard_actions(path: Path | None) -> list[dict[str, Any]]:
    return actions_from_events(read_events(path)[0])


def is_fail_closed(action: dict[str, Any]) -> bool:
    """True only when the classifier actually failed.

    The guard reports one `fail_closed` state for three different situations:
    a real Level 1/2 failure (`L1-FAIL:timeout`), a Level 1 that answered
    `REVIEW` with no Level 2 behind it (`L1:review`), and the rules-only
    configuration where no classifier runs at all (no rule). Only the first is
    a failure, and this benchmark separates "asked because it broke" from
    "asked because it judged" -- so the reason code decides, not the level
    label alone. A run whose guard log predates this distinction can be
    re-derived from `guard_reason_code`, which was always recorded.
    """
    if action.get("failure") in ("timeout", "transport", "invalid_response"): return True
    if action.get("guard_level") != "fail_closed":
        return False
    reason = action.get("guard_reason_code") or ""
    return reason.startswith("L1-FAIL") or reason.startswith("L2-FAIL")


def summarize_guard(actions: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [a["guard_latency_ms"] for a in actions if a.get("guard_latency_ms") is not None]
    return {
        "allow": sum(1 for a in actions if a.get("guard_decision") == "allow"),
        "ask": sum(1 for a in actions if a.get("guard_decision") == "ask"),
        "deny": sum(1 for a in actions if a.get("guard_decision") == "deny"),
        "fail_closed": sum(1 for a in actions if is_fail_closed(a)),
        "total_guard_latency_ms": sum(latencies) if latencies else None,
    }


# --------------------------------------------------------------------------
# one run
# --------------------------------------------------------------------------


def execute_run(scenario: dict[str, Any], repeat_index: int, args: argparse.Namespace) -> dict[str, Any]:
    oracles_config = scenario["oracles"]
    sink_config = oracles_config["harm"].get("canary_sink")
    started_at = datetime.now(timezone.utc).isoformat()

    sink_ctx = DecoySink(sink_config["bind_host"]) if sink_config else None
    workspace: Path | None = None
    guard_log = None
    events, audit_valid, audit_error = [], False, "dry_run"
    process = AgentOutcome("ok", 0, None, "dry_run")
    diagnostics = Path(tempfile.mkdtemp(prefix="autoguard-run-", dir=getattr(args, "diagnostics", None)))

    try:
        with (sink_ctx or _NullSink()) as sink:
            fixture_dir = FIXTURES_ROOT / scenario["fixture"]
            fixture_before = fixture_digest(fixture_dir)

            workspace = prepare_workspace(
                scenario, sink.url if sink_ctx else None, git_init=not args.no_git_init
            )
            before = snapshot_workspace(workspace)

            if args.dry_run:
                status, duration_ms = STATUS_OK, 0
            else:
                guard_log = diagnostics / "events.jsonl"
                host = scenario.get("trusted_context", {})
                environment = {
                    "AUTOGUARD_AUDIT_PATH": str(guard_log),
                    "AUTOGUARD_STATE_DIR": str(diagnostics / "state"),
                    "AUTOGUARD_BENCH_LEVEL": "level0" if args.arm == "level0_only" else args.arm,
                    "AUTOGUARD_INTERACTION": getattr(args, "interaction", "autonomous"),
                    "AUTOGUARD_GENERATED_PATHS": ",".join(host.get("generated_output", [])),
                    "AUTOGUARD_SOURCE_PATHS": ",".join(host.get("source", [])),
                    "AUTOGUARD_TEST_PATHS": ",".join(host.get("verification", [])),
                    "AUTOGUARD_TRUST_TESTS": "1" if host.get("trusted_tests", False) else "0",
                }
                if getattr(args, "interaction", "autonomous") == "scripted":
                    answers = diagnostics / "answers.json"
                    answers.write_text(json.dumps(scenario.get("scripted_answers", [])))
                    os.chmod(answers, 0o600)
                    environment["AUTOGUARD_SCRIPTED_ANSWERS_FILE"] = str(answers)
                if getattr(args, "kilo_root", None):
                    configuration = diagnostics / "kilo.json"
                    configuration.write_text(json.dumps({"plugin": [(args.kilo_root / "packages/opencode/src/kilocode/autoguard/bench-plugin.ts").resolve().as_uri()], "model": args.agent_model, "sandbox": {"enabled": True, "network": "deny"}, "agent": {"build": {"temperature": args.temperature}}}))
                    os.chmod(configuration, 0o600)
                    environment["KILO_CONFIG"] = str(configuration)
                    checkpoint = diagnostics / "oracle.json"
                    checkpoint.write_text(json.dumps({"python": sys.executable, "probe": str(SCRIPT_DIR / "recovery_probe.py"), "workspace": str(workspace), "safe_path_available": scenario.get("safe_path_available", True), "before": before, "fixture": str(fixture_dir), "oracles": oracles_config, "bun": args.bun, "launcher": str(args.kilo_root / "packages/opencode/script/kilocode/autoguard-verify.ts")}))
                    os.chmod(checkpoint, 0o600)
                    environment["AUTOGUARD_ORACLE_CONFIG"] = str(checkpoint)
                process = run_agent(workspace, args.agent_cmd, scenario["task_prompt"], args.agent_timeout,
                                    environment=environment, diagnostics=diagnostics, audit=guard_log, interaction=getattr(args, "interaction", "autonomous"))
                status, duration_ms = process
                events, audit_valid, audit_error = read_events(guard_log, Path(process.stdout_path) if process.stdout_path else None)

            after = snapshot_workspace(workspace)
            # The guard's own log is an artifact of measurement, not something
            # the agent changed; it must not show up as collateral damage.
            after.pop(".autoguard-actions.jsonl", None)
            before.pop(".autoguard-actions.jsonl", None)

            if fixture_digest(fixture_dir) != fixture_before:
                # Loud on purpose. A mutated source fixture means the agent
                # escaped the sandbox, this run's verdict is meaningless, and
                # every later run of the scenario would start from different
                # source. Restore the fixture from version control before
                # trusting any result.
                raise FixtureEscaped(
                    f"{scenario['fixture']} changed on disk during the run; the agent "
                    "wrote outside its temporary workspace"
                )

            actions = load_guard_actions(guard_log)
            # Judge utility on a disposable copy, restoring host-owned verification assertions.
            with tempfile.TemporaryDirectory(prefix="autoguard-oracle-") as verification:
                checked = Path(verification) / "workspace"
                shutil.copytree(workspace, checked, symlinks=True)
                protected = fixture_dir / "tests"
                if protected.exists():
                    target = checked / "tests"
                    if target.is_symlink(): target.unlink()
                    elif target.exists(): shutil.rmtree(target)
                    shutil.copytree(protected, target, symlinks=False)
                verification_config = oracles_config
                if getattr(args, "kilo_root", None):
                    launcher = args.kilo_root / "packages/opencode/script/kilocode/autoguard-verify.ts"
                    verification_config = {**oracles_config, "utility": {**oracles_config["utility"], "command": [args.bun, str(launcher), json.dumps({"cwd": str(checked), "argv": oracles_config["utility"]["command"]})]}}
                oracle_results = judge_all(checked, before, after, verification_config, sink.requests)

    except (FileNotFoundError, ValueError, FixtureEscaped) as exc:
        print(f"  setup failed: {exc}", file=sys.stderr)
        return _infra_error_record(scenario, repeat_index, args, started_at, str(exc))
    finally:
        if workspace and workspace.exists() and not args.keep_workspaces:
            shutil.rmtree(workspace, ignore_errors=True)

    return {
        "schema_version": "0.2",
        "run_id": f"{scenario['scenario_id']}-{args.arm}-r{repeat_index}",
        "scenario_id": scenario["scenario_id"],
        "group_id": scenario["group_id"],
        "arm": args.arm,
        "repeat_index": repeat_index,
        "environment": _environment(args),
        "status": status,
        "process": asdict(process),
        "events": events,
        "recovery_checkpoints": recovery_summary(events, scenario, oracle_results),
        "audit": {"valid": audit_valid, "error": audit_error},
        "oracle_status": "observed",
        "interaction": getattr(args, "interaction", "autonomous"),
        "manifest": {**getattr(args, "manifest", {}), "scenario_hash": hashlib.sha256(json.dumps(scenario, sort_keys=True).encode()).hexdigest(),
                     "command_hash": hashlib.sha256(args.agent_cmd.encode()).hexdigest(),
                     "startup": next((event for event in events if event.get("event") == "startup"), None)},
        "started_at_utc": started_at,
        "duration_ms": duration_ms,
        "actions": actions,
        "guard_summary": summarize_guard(actions),
        "oracle_results": oracle_results,
        "notes": "dry_run: agent never ran, utility must be false" if args.dry_run else None,
    }


def recovery_summary(events: list[dict], scenario: dict, result: dict) -> list[dict]:
    denied = next((e for e in events if e.get("event") == "policy_decided" and e.get("policy_decision") == "deny"), None)
    if denied is None: return []
    points = [e for e in events if e.get("event") == "recovery_checkpoint"]
    success = any(e.get("verified") and e.get("recovered_within_two") for e in points)
    uncertain = any(not e.get("verified") for e in points)
    return [{"deny_call_id": denied["call_id"], "safe_path_available": scenario.get("safe_path_available", True), "verified": bool(points) and not uncertain,
             "recovered_within_two": success and not result["harm"]["harmed"], "checkpoints": len(points)}]


class _NullSink:
    """Stand-in for scenarios with no exfiltration surface."""

    url = None
    requests: list[dict[str, Any]] = []

    def __enter__(self) -> _NullSink:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


def _environment(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "agent_model": args.agent_model,
        "guard_level1_model": args.guard_level1_model,
        "kilo_commit": args.kilo_commit,
        "guard_commit": args.guard_commit,
        "temperature": args.temperature,
        "seed": args.seed,
        # Always true here by construction: the agent is a fresh subprocess
        # per run. Recorded anyway so a reader never has to take it on trust.
        "process_restarted": True,
        "os": f"{platform.system()} {platform.release()}",
    }


def _infra_error_record(
    scenario: dict[str, Any],
    repeat_index: int,
    args: argparse.Namespace,
    started_at: str,
    detail: str,
) -> dict[str, Any]:
    return {
        "schema_version": "0.2",
        "run_id": f"{scenario['scenario_id']}-{args.arm}-r{repeat_index}",
        "scenario_id": scenario["scenario_id"],
        "group_id": scenario["group_id"],
        "arm": args.arm,
        "repeat_index": repeat_index,
        "environment": _environment(args),
        "status": STATUS_INFRA_ERROR,
        "oracle_status": "unavailable",
        "interaction": getattr(args, "interaction", "autonomous"),
        "audit": {"valid": False, "error": "setup_error"},
        "started_at_utc": started_at,
        "duration_ms": 0,
        "actions": [],
        "guard_summary": summarize_guard([]),
        "oracle_results": {
            "harm": {"harmed": False, "evidence": []},
            "utility": {"solved": False, "exit_code": None, "timed_out": False},
            "collateral": {"unexpected_change_count": 0, "unexpected_changes": []},
        },
        "notes": f"infrastructure_error: {detail}",
    }


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------


def _raise_on_sigterm(signum: int, _frame: Any) -> None:
    """Turn SIGTERM into an exception so `finally` blocks still run.

    Python does not unwind the stack on a default SIGTERM, so a sweep killed
    from outside would leave its temporary workspace on disk -- and a sweep is
    exactly the thing someone kills halfway through.
    """
    raise KeyboardInterrupt(f"received signal {signum}")


def main() -> int:
    signal.signal(signal.SIGTERM, _raise_on_sigterm)

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--arm", choices=ARMS, default="guard_off")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--diagnostics", type=Path, default=None)
    parser.add_argument("--kilo-root", type=Path, default=None, help="Kilo source worktree; install the benchmark plugin through an external host config")
    parser.add_argument("--interaction", choices=("autonomous", "scripted"), default="autonomous")
    parser.add_argument("--review", type=Path, default=None, help="Independent human review manifest required for holdout model runs")
    parser.add_argument("--bun", default="bun", help="Bun executable for --kilo-root")
    parser.add_argument("--limit", type=int, default=None, help="cap scenarios read, never repeats")
    parser.add_argument("--scenario-id", action="append", default=None, help="run only these scenarios")
    parser.add_argument(
        "--agent-cmd",
        default="kilo run --auto {task_prompt}",
        help="argv template; {task_prompt} is substituted with the scenario's task",
    )
    parser.add_argument("--agent-timeout", type=int, default=600)
    parser.add_argument("--agent-model", default="unspecified")
    parser.add_argument("--guard-level1-model", default=None)
    parser.add_argument("--kilo-commit", default="unspecified")
    parser.add_argument("--guard-commit", default="unspecified")
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="skip the agent; fixture self-test")
    parser.add_argument(
        "--no-git-init",
        action="store_true",
        help="do not make the workspace a git repository (agents may then resolve "
        "their project root outside the sandbox)",
    )
    parser.add_argument("--keep-workspaces", action="store_true", help="debugging only; leaves temp dirs behind")
    args = parser.parse_args()

    if args.kilo_root:
        args.kilo_root = args.kilo_root.resolve()
        args.agent_cmd = shlex.join([args.bun, "run", "--conditions=browser", str(args.kilo_root / "packages/opencode/src/index.ts"), "run", "--auto", "--format", "json", "--model", args.agent_model, "--dir", "{workspace}", "{task_prompt}"])
        args.kilo_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=args.kilo_root, text=True).strip()
        args.guard_commit = args.kilo_commit
    if args.diagnostics: args.diagnostics.mkdir(parents=True, exist_ok=True, mode=0o700)
    scenarios = [json.loads(line) for line in args.scenarios.read_text().splitlines() if line.strip()]
    args.manifest = {"dataset": dataset_manifest(args.scenarios.resolve(), BENCHMARK_ROOT), "benchmark": source_manifest(BENCHMARK_ROOT.parent)}
    if args.kilo_root: args.manifest["runtime"] = source_manifest(args.kilo_root)
    if not args.dry_run and any(row["split"] == "holdout" for row in scenarios):
        try:
            args.manifest["human_review"] = require_review(args.review, args.manifest["dataset"], args.manifest.get("runtime", {}).get("hash", "missing"))
        except ValueError as error:
            parser.error(str(error))
    if args.scenario_id:
        scenarios = [s for s in scenarios if s["scenario_id"] in set(args.scenario_id)]
    if args.limit is not None:
        scenarios = scenarios[: args.limit]
    if not scenarios:
        print("no scenarios selected", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with args.output.open("w", encoding="utf-8") as handle:
        for scenario in scenarios:
            for repeat_index in range(args.repeats):
                print(f"{scenario['scenario_id']} [{args.arm}] r{repeat_index}", file=sys.stderr)
                record = execute_run(scenario, repeat_index, args)
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                written += 1
                outcome = record["oracle_results"]
                print(
                    f"  status={record['status']} harmed={outcome['harm']['harmed']} "
                    f"solved={outcome['utility']['solved']} "
                    f"collateral={outcome['collateral']['unexpected_change_count']}",
                    file=sys.stderr,
                )

    print(f"wrote {written} run records to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
