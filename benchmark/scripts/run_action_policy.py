#!/usr/bin/env python3
"""Minimal OpenRouter-compatible inference runner for the action-policy suite.

This script sends each benchmark case's *policy-visible* `input` (never the
surrounding case/label metadata) to a model over the OpenRouter chat
completions API, asks it to return a strict JSON decision, and writes one
JSONL result record per trial to `--output`, flushing immediately after each
write so a partial run survives interruption.

It never loads `labels.jsonl` and never executes `proposed_action` -- it only
asks a model to classify it. Scoring against ground truth happens later, out
of process, in `score_action_policy.py`.

Usage:
    export OPENROUTER_API_KEY=...
    python3 run_action_policy.py --model qwen/qwen3.8-flash \\
        --view full_context --output results.jsonl

    # Repeat --view to run every case through multiple views in one
    # invocation, one trial per case per requested view (order preserved,
    # duplicates rejected):
    python3 run_action_policy.py --model qwen/qwen3.8-flash \\
        --view action_only --view intent_action --view full_context \\
        --output results.jsonl

    # Preview requests without calling the network or requiring an API key:
    python3 run_action_policy.py --model qwen/qwen3.8-flash \\
        --view full_context --output /tmp/dry.jsonl --dry-run --limit 1

Exit status is 0 when the run completes (including runs that recorded
`invalid_output`/`api_error`/`timeout` trials -- those are data, not script
failures), non-zero on usage errors (bad arguments, missing API key, unreadable
cases file).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from jsonschema import Draft202012Validator

SCRIPT_DIR = Path(__file__).resolve().parent
BENCHMARK_ROOT = SCRIPT_DIR.parent

DEFAULT_CASES_PATH = BENCHMARK_ROOT / "datasets" / "action-policy" / "dev" / "cases.jsonl"
PROMPT_PATH = BENCHMARK_ROOT / "prompts" / "action-policy-v1.md"
PREDICTION_SCHEMA_PATH = BENCHMARK_ROOT / "schemas" / "action-prediction.schema.json"

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
API_KEY_ENV_VAR = "OPENROUTER_API_KEY"

VIEWS = ("action_only", "intent_action", "full_context")

# Fields that must never appear anywhere in the payload sent to the model.
# These are benchmark/ground-truth metadata that lives on the case record or
# in labels.jsonl (which this script never even loads), not in case["input"].
FORBIDDEN_KEYS = {
    "case_id",
    "group_id",
    "family",
    "provenance",
    "schema_version",
    "split",
    "expected_decision",
    "risk_level",
    "review_status",
    "labels",
}

STATUS_OK = "ok"
STATUS_INVALID_OUTPUT = "invalid_output"
STATUS_API_ERROR = "api_error"
STATUS_TIMEOUT = "timeout"
STATUS_DRY_RUN = "dry_run"


class ForbiddenFieldError(RuntimeError):
    """Raised if benchmark metadata would leak into a model-visible payload."""


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_prompt(path: Path = PROMPT_PATH) -> str:
    return path.read_text(encoding="utf-8")


def load_prediction_validator(path: Path = PREDICTION_SCHEMA_PATH) -> Draft202012Validator:
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def load_cases(path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Read cases.jsonl, keeping only records shaped like a case (case_id + input).

    This script does not perform full schema validation (that is
    validate_action_policy.py's job) -- it only needs enough structure to
    build a view. Malformed lines are skipped with a warning on stderr rather
    than raising, so a runner invocation never crashes on a single bad line.
    """
    cases: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                print(f"warning: {path}:{line_no}: invalid JSON ({exc.msg}), skipping", file=sys.stderr)
                continue
            if not isinstance(record, dict) or "case_id" not in record or "input" not in record:
                print(f"warning: {path}:{line_no}: not a case record, skipping", file=sys.stderr)
                continue
            cases.append(record)
            if limit is not None and len(cases) >= limit:
                break
    return cases


def _walk_forbidden_keys(payload: Any, forbidden: set[str], path: str = "$") -> str | None:
    """Return a description of the first forbidden key found, or None."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in forbidden:
                return f"{path}.{key}"
            hit = _walk_forbidden_keys(value, forbidden, f"{path}.{key}")
            if hit is not None:
                return hit
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            hit = _walk_forbidden_keys(item, forbidden, f"{path}[{index}]")
            if hit is not None:
                return hit
    return None


def assert_no_forbidden_keys(payload: Any, forbidden: set[str] = FORBIDDEN_KEYS) -> None:
    hit = _walk_forbidden_keys(payload, forbidden)
    if hit is not None:
        raise ForbiddenFieldError(f"forbidden benchmark metadata key found at {hit}")


def build_view_payload(case_input: dict[str, Any], view: str) -> dict[str, Any]:
    """Project case["input"] down to exactly what a given view is allowed to see."""
    if view == "action_only":
        payload = {"proposed_action": case_input["proposed_action"]}
    elif view == "intent_action":
        payload = {
            "user_intent": case_input["user_intent"],
            "proposed_action": case_input["proposed_action"],
        }
    elif view == "full_context":
        payload = case_input
    else:
        raise ValueError(f"unknown view: {view!r} (expected one of {VIEWS})")
    payload = copy.deepcopy(payload)
    assert_no_forbidden_keys(payload)
    return payload


def render_messages(system_prompt: str, view_payload: dict[str, Any]) -> list[dict[str, str]]:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(view_payload, sort_keys=True)},
    ]
    assert_no_forbidden_keys(messages)
    return messages


def strip_code_fence(text: str) -> str:
    """Strip a single leading/trailing ```json ... ``` or ``` ... ``` fence, if present."""
    stripped = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return stripped


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse model output text into a JSON object. Raises ValueError on failure.

    Accepts a bare JSON object or one wrapped in a single ```json fence.
    Does not attempt any other repair -- malformed output must surface as
    invalid_output, not be silently coerced into a guess.
    """
    cleaned = strip_code_fence(text)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"parsed JSON is not an object (got {type(value).__name__})")
    return value


@dataclass
class TrialResult:
    case_id: str
    view: str
    repeat_index: int
    requested_model: str
    seed: int | None
    status: str
    prediction: dict[str, Any] | None = None
    raw_response_text: str | None = None
    error: str | None = None
    latency_ms: float | None = None
    usage: dict[str, Any] | None = None
    provider: Any = None
    dry_run_request: dict[str, Any] | None = None
    timestamp_utc: str = field(default_factory=iso_now)

    def to_record(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "view": self.view,
            "repeat_index": self.repeat_index,
            "requested_model": self.requested_model,
            "seed": self.seed,
            "status": self.status,
            "prediction": self.prediction,
            "raw_response_text": self.raw_response_text,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "usage": self.usage,
            "provider": self.provider,
            "dry_run_request": self.dry_run_request,
            "timestamp_utc": self.timestamp_utc,
        }


def call_openrouter(
    client: httpx.Client,
    *,
    model: str,
    messages: list[dict[str, str]],
    seed: int | None,
    timeout: float,
) -> tuple[str, str | None, str | None, float, dict[str, Any] | None, Any]:
    """Issue one chat-completions call. Never raises: failures are returned as data.

    Returns (status, content, error, latency_ms, usage, provider) where status
    is one of STATUS_OK (meaning "got a response body to parse", not yet
    validated), STATUS_API_ERROR, or STATUS_TIMEOUT.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
    }
    if seed is not None:
        payload["seed"] = seed

    start = time.perf_counter()
    try:
        response = client.post("/chat/completions", json=payload, timeout=timeout)
    except httpx.TimeoutException as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return STATUS_TIMEOUT, None, f"request timed out: {exc}", latency_ms, None, None
    except httpx.HTTPError as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return STATUS_API_ERROR, None, f"transport error: {exc}", latency_ms, None, None
    latency_ms = (time.perf_counter() - start) * 1000

    if response.status_code >= 400:
        body_preview = response.text[:500]
        return (
            STATUS_API_ERROR,
            None,
            f"HTTP {response.status_code}: {body_preview}",
            latency_ms,
            None,
            None,
        )

    try:
        body = response.json()
    except ValueError as exc:
        return STATUS_API_ERROR, None, f"non-JSON API response: {exc}", latency_ms, None, None

    usage = body.get("usage") if isinstance(body, dict) else None
    provider = body.get("provider") if isinstance(body, dict) else None

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        return (
            STATUS_API_ERROR,
            None,
            f"unexpected API response shape: {exc}",
            latency_ms,
            usage,
            provider,
        )

    if not isinstance(content, str):
        return (
            STATUS_API_ERROR,
            None,
            f"unexpected message content type: {type(content).__name__}",
            latency_ms,
            usage,
            provider,
        )

    return STATUS_OK, content, None, latency_ms, usage, provider


def run_single_trial(
    client: httpx.Client,
    *,
    model: str,
    system_prompt: str,
    case: dict[str, Any],
    view: str,
    repeat_index: int,
    seed: int | None,
    timeout: float,
    validator: Draft202012Validator,
) -> TrialResult:
    case_id = case["case_id"]
    view_payload = build_view_payload(case["input"], view)
    messages = render_messages(system_prompt, view_payload)

    status, content, error, latency_ms, usage, provider = call_openrouter(
        client, model=model, messages=messages, seed=seed, timeout=timeout
    )

    prediction: dict[str, Any] | None = None
    if status == STATUS_OK:
        try:
            parsed = parse_json_object(content)  # type: ignore[arg-type]
        except ValueError as exc:
            status = STATUS_INVALID_OUTPUT
            error = str(exc)
        else:
            schema_errors = list(validator.iter_errors(parsed))
            if schema_errors:
                status = STATUS_INVALID_OUTPUT
                error = "; ".join(sorted(e.message for e in schema_errors))
            else:
                prediction = parsed

    return TrialResult(
        case_id=case_id,
        view=view,
        repeat_index=repeat_index,
        requested_model=model,
        seed=seed,
        status=status,
        prediction=prediction,
        raw_response_text=content,
        error=error,
        latency_ms=latency_ms,
        usage=usage,
        provider=provider,
    )


def run_dry_trial(
    *,
    model: str,
    system_prompt: str,
    case: dict[str, Any],
    view: str,
    repeat_index: int,
    seed: int | None,
) -> TrialResult:
    """Build (and safety-check) the request that would be sent, without any network call."""
    case_id = case["case_id"]
    view_payload = build_view_payload(case["input"], view)
    messages = render_messages(system_prompt, view_payload)
    return TrialResult(
        case_id=case_id,
        view=view,
        repeat_index=repeat_index,
        requested_model=model,
        seed=seed,
        status=STATUS_DRY_RUN,
        prediction=None,
        raw_response_text=None,
        error=None,
        latency_ms=None,
        usage=None,
        provider=None,
        dry_run_request={"model": model, "messages": messages, "temperature": 0, "seed": seed},
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help=f"Path to cases JSONL file (default: {DEFAULT_CASES_PATH})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path to write JSONL trial results to (overwritten at the start of the run).",
    )
    parser.add_argument(
        "--model",
        required=True,
        help="Direct OpenRouter model ID, e.g. qwen/qwen3.8-flash.",
    )
    parser.add_argument(
        "--view",
        dest="views",
        action="append",
        required=True,
        choices=VIEWS,
        help=(
            "Which projection of case input to send to the model. Repeatable "
            "(e.g. --view action_only --view full_context) to run every case "
            "through multiple views in one invocation, one trial per case per "
            "requested view; the order views are given in is preserved and "
            "each view runs exactly once per case (duplicates are rejected)."
        ),
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Number of independent repeats per case (default: 1).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N cases (default: all).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Per-request timeout in seconds (default: 60).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional seed to request and record (default: none sent).",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"OpenRouter-compatible API base URL (default: {DEFAULT_BASE_URL}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build and safety-check requests without calling the network or requiring an API key.",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_arg_parser().parse_args(argv)


def _make_client(base_url: str, api_key: str) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )


def run(
    args: argparse.Namespace,
    *,
    client: httpx.Client | None = None,
) -> int:
    """Execute the run described by `args`, writing results to args.output.

    `client` may be injected for testing (e.g. an httpx.Client backed by
    httpx.MockTransport); when omitted, a real client authenticated from
    OPENROUTER_API_KEY is created for a non-dry-run invocation.
    """
    if args.repeats < 1:
        print("error: --repeats must be >= 1", file=sys.stderr)
        return 2
    if args.limit is not None and args.limit < 0:
        print("error: --limit must be >= 0", file=sys.stderr)
        return 2
    if not args.views:
        print("error: at least one --view is required", file=sys.stderr)
        return 2
    duplicate_views = sorted({v for v in args.views if args.views.count(v) > 1})
    if duplicate_views:
        print(
            "error: --view was given more than once for: "
            f"{', '.join(duplicate_views)} (each view must be requested at most "
            "once, to avoid spending extra API calls on accidental duplicates)",
            file=sys.stderr,
        )
        return 2

    try:
        system_prompt = load_prompt()
    except OSError as exc:
        print(f"error: could not read prompt file: {exc}", file=sys.stderr)
        return 2

    try:
        validator = load_prediction_validator()
    except Exception as exc:  # noqa: BLE001 - report and exit cleanly, never traceback on bad schema
        print(f"error: could not load prediction schema: {exc}", file=sys.stderr)
        return 2

    try:
        cases = load_cases(args.cases, limit=args.limit)
    except OSError as exc:
        print(f"error: could not read cases file: {exc}", file=sys.stderr)
        return 2

    if not cases:
        print(f"error: no usable cases found in {args.cases}", file=sys.stderr)
        return 2

    owns_client = False
    if not args.dry_run and client is None:
        api_key = os.environ.get(API_KEY_ENV_VAR)
        if not api_key:
            print(
                f"error: {API_KEY_ENV_VAR} is not set (required unless --dry-run is passed)",
                file=sys.stderr,
            )
            return 2
        client = _make_client(args.base_url, api_key)
        owns_client = True

    counts: dict[str, int] = {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.output.open("w", encoding="utf-8") as out_f:
            for case in cases:
                for view in args.views:
                    for repeat_index in range(args.repeats):
                        if args.dry_run:
                            result = run_dry_trial(
                                model=args.model,
                                system_prompt=system_prompt,
                                case=case,
                                view=view,
                                repeat_index=repeat_index,
                                seed=args.seed,
                            )
                        else:
                            assert client is not None
                            result = run_single_trial(
                                client,
                                model=args.model,
                                system_prompt=system_prompt,
                                case=case,
                                view=view,
                                repeat_index=repeat_index,
                                seed=args.seed,
                                timeout=args.timeout,
                                validator=validator,
                            )
                        counts[result.status] = counts.get(result.status, 0) + 1
                        out_f.write(json.dumps(result.to_record()))
                        out_f.write("\n")
                        out_f.flush()
                        os.fsync(out_f.fileno())
    finally:
        if owns_client and client is not None:
            client.close()

    total = sum(counts.values())
    summary = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    print(f"wrote {total} trial result(s) to {args.output} ({summary})")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
