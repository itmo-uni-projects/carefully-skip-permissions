"""Versioned guard audit reader. Decisions are never execution evidence."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_events(path: Path | None, protocol: Path | None = None) -> tuple[list[dict[str, Any]], bool, str | None]:
    if path is None or not path.exists():
        return [], False, "missing_audit"
    try:
        events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except (OSError, ValueError) as exc:
        return [], False, f"invalid_audit:{type(exc).__name__}"
    if not all(isinstance(event, dict) for event in events):
        return [], False, "invalid_audit_event"
    startup = [event for event in events if event.get("event") == "startup"]
    if len(startup) != 1: return events, False, "missing_or_duplicate_startup"
    if startup[0].get("runtime_required") and startup[0].get("runtime_supported") is not True:
        return events, False, "unsupported_runtime"
    if events[0] is not startup[0]: return events, False, "startup_out_of_order"
    calls = {}
    for event in events[1:]:
        kind = event.get("event")
        key = (event.get("session_id"), event.get("call_id"))
        if kind == "proposed":
            if not isinstance(event.get("normalized"), list) or not event.get("tool"):
                return events, False, "invalid_proposal"
            calls[key] = {"decided": False, "count": len(event["normalized"]), "finished": False, "decision": None}
        if kind in ("policy_decided", "execution_started", "execution_finished", "tool_finished"):
            call = calls.get(key)
            if call is None: return events, False, "event_without_proposal"
            if kind == "policy_decided":
                call["decided"], call["decision"] = True, event.get("policy_decision")
            elif not call["decided"]: return events, False, "effect_without_decision"
            if kind == "tool_finished": call["finished"] = True
            if kind.startswith("execution_") and event.get("operation_index") not in range(call["count"]):
                return events, False, "invalid_operation_index"
    if protocol and protocol.exists():
        for line in protocol.read_text(errors="replace").splitlines():
            try: event = json.loads(line)
            except ValueError: continue
            if not isinstance(event, dict) or event.get("type") != "tool_use": continue
            part = event.get("part", {})
            call = calls.get((part.get("sessionID", event.get("sessionID")), part.get("callID")))
            if call is None:
                # Kilo rejects hallucinated tool IDs before an adapter can run.
                error = part.get("state", {}).get("error", "")
                if isinstance(error, str) and error.startswith(f"Model tried to call unavailable tool '{part.get('tool')}'. Available tools: "):
                    events.append({"schema_version": "0.2", "event": "native_rejected", "timestamp": datetime.fromtimestamp(event["timestamp"] / 1000, timezone.utc).isoformat(), "session_id": part.get("sessionID", event.get("sessionID")), "call_id": part["callID"], "tool": part["tool"], "reason_code": "unavailable_tool", "evidence": "protected_kilo_protocol"})
                    continue
                return events, False, "native_tool_missing_from_audit"
            if part.get("state", {}).get("status") in ("completed", "error") and call["decision"] not in ("ask", "deny") and not call["finished"]:
                return events, False, "native_tool_outcome_missing_from_audit"
    if any(event.get("event") == "native_rejected" for event in events):
        events.sort(key=lambda e: datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")))
    return events, True, None


def actions_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if events and "event" not in events[0]:
        return [{**event, "executed": None, "execution_evidence": "legacy_unknown"} for event in events]
    calls: dict[tuple[str, str], dict[str, Any]] = {}
    records: list[dict[str, Any]] = []
    for event in events:
        key = (event.get("session_id", ""), event.get("call_id", ""))
        kind = event.get("event")
        if kind == "native_rejected":
            records.append({"sequence": len(records), "session_id": key[0], "call_id": key[1], "tool": event["tool"], "normalized_actions": [], "guard_decision": None, "guard_level": None, "guard_reason_code": None, "guard_latency_ms": None, "executed": False, "execution_evidence": "native_rejected", "execution_events": [], "error": event["reason_code"]})
            continue
        if kind == "proposed":
            calls[key] = {"sequence": len(records), "session_id": key[0], "call_id": key[1], "tool": event["tool"], "normalized_actions": event["normalized"], "guard_decision": None, "guard_level": None, "guard_reason_code": None, "guard_latency_ms": None, "executed": None, "execution_evidence": "not_observed", "execution_events": []}
            records.append(calls[key])
        call = calls.get(key)
        if call is None:
            continue
        if kind == "policy_decided":
            decision = event.get("decision") or {}
            call.update(guard_decision=event.get("policy_decision"), guard_level=decision.get("decided_by"), guard_reason_code=decision.get("reason_code") or decision.get("rule"), guard_latency_ms=decision.get("latency_ms"), failure=decision.get("failure"))
            call["l1_latency_ms"] = [r["level1"]["latency_ms"] for r in event.get("operation_results", []) if r and r.get("level1") and r["level1"].get("latency_ms") is not None]
            if event.get("policy_decision") in ("deny", "ask"):
                call.update(executed=False, execution_evidence="policy_blocked")
        if kind in ("execution_started", "execution_finished"):
            call["execution_events"].append(event)
            call["execution_evidence"] = "runtime_boundary"
        if kind == "tool_finished":
            call.update(executed=event.get("executed"), exit_code=event.get("exit_code"), error=event.get("error"))
    return records


def waiting(events: list[dict[str, Any]]) -> bool:
    pending = {event["request_id"] for event in events if event.get("event") == "waiting_user"}
    replied = {event["request_id"] for event in events if event.get("event") == "approval_replied"}
    return bool(pending - replied)
