# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

Hackathon project for the Alfa-Bank case `--carefully-skip-permissions` (ITMO, team 5, deadlines 04.09 interim / 07.09 final 2026). Goal: build a Claude-Code-style **auto mode** for the open-source coding agent **Kilo Code**, plus a benchmark that proves it works.

**Right now this repo contains documentation only — no code, no build system, no tests.** Working docs in `docs/` are the source of truth for scope and design decisions. Read them before writing anything:

- `docs/case-description.md` — the customer's case brief (RU): required deliverables, metrics, grading criteria.
- `docs/CONTEXT_PACK_v3_full.md` — the living product/eng plan (RU): MVP boundaries, hypotheses, success thresholds, kill criteria, per-person ownership. Update this when scope changes; it marks each claim as `[Кейсодатель] / [Ресерч] / [Решение команды] / [Гипотеза]`.
- `docs/classification-design.md` — how a single outgoing tool call is classified (RU): label spaces, risk axes, normalized-action schema, Level 0/1/2 contracts, cascade transition rules, anti-evasion. Owned by AI Engineer 2.
- `docs/benchmark-dataset-design.md` — dataset grid and labeling schema (RU): four pools, 13 effect×radius cells, minimal pairs, provenance multiplier, run budget, per-case record schema. Grid owned by AI Engineer 2, population and runs by AI Engineer 1.
- `docs/kilo-code-overview.md` — verified technical map of `Kilo-Org/kilocode` @ v7.5.9: file paths, line numbers, existing permission engine, sandbox, plugin hooks, gap analysis, implementation tiers.
- `docs/meeting-notes/01-meeting-with-nikita.md` — kickoff with the case owner (RU): threat classes, reference architecture, how the provided benchmark harness works, what the judges actually reward.

Documentation language is Russian for product/planning docs, English for the technical overview. Keep that split.

## The code lives in a separate fork

Implementation target is **Kilo CLI** — `packages/opencode/` inside our fork https://github.com/itmo-uni-projects/kilocode (upstream `Kilo-Org/kilocode`). Not the VS Code extension: only the CLI can be driven headlessly (`kilo run "<task>"`) by the benchmark harness.

Dev loop inside that fork (repo pins bun@1.3.14):

```bash
bun install
bun run dev                                  # run CLI from source
bun turbo typecheck                          # scope to one package; whole-tree is slow
bun run --cwd packages/opencode test
bun run lint                                 # oxlint
bun run check:duplication                    # enforces the kilocode_change convention
```

**Fork convention (enforced by `script/check-kilocode-duplication.ts`):** everything under `packages/opencode/src/**` is upstream opencode code — inline edits there must be marked `// kilocode_change`. Kilo-only code goes in `packages/opencode/src/kilocode/**`, tests in `packages/opencode/test/kilocode/**`. Follow it so `git merge upstream` stays cheap and a PR back to Kilo stays reviewable.

## Architecture we are building

A **multi-level cascade** evaluating *outgoing* tool calls just before execution. Inbound prompt-injection screening, slopsquatting, and a custom sandbox are explicitly out of MVP scope (decision recorded in CONTEXT_PACK §4).

- **Level 0 — deterministic rules.** Narrow safe allowlist + hard deny for a chosen catastrophic class. This is the control that must be *architecturally* immune to prompt injection (case requirement): no LLM sits in its decision path, so no injected text can talk it into an allow.
- **Level 1 — fast classifier.** Runs on every non-trivial action, low latency, returns `ALLOW / REVIEW / DENY`, biased toward flagging.
- **Level 2 — deep classifier.** Only on `REVIEW`. Receives the direct user request, the normalized tool call, trusted environment description, and bounded action history.
- **Output:** `allow / deny / ask` + reason + risk level + safe continuation.

Two rules that are load-bearing, not cosmetic:

1. **Fail-closed:** classifier timeout, unavailability, or malformed response → `ask`, never automatic `allow`.
2. **The classifier never sees raw tool results.** Tool output is exactly where hostile content enters. Feeding it to the reviewer is what makes reviewer agents weak (see Goose Adversary Mode). Pass only user intent, the normalized action, trusted environment facts, and structured action history.

**Deny-and-continue:** a denial is a structured tool result the agent can reason about and route around, not a bare rejection error. Escalation backstop halts to a human after repeated denials.

### Integration point

Preferred: **Tier 0 — external plugin, zero fork.** `packages/plugin` exposes `permission.ask` (set `output.status`), `tool.execute.before` (read `output.args`), and `tool.execute.after` (rewrite `output.output`). Exposing the permission decision itself is unusual and means the full auto-mode shape is reachable without modifying the monorepo — which also gives a clean plugin-on/plugin-off A/B for the benchmark.

Fall back to **Tier 1 — fork, two files** only when the plugin API cannot express a hard ruleset: `packages/opencode/src/kilocode/session/prompt.ts:372` (where the ruleset is assembled and `permission.ask` fires) and `packages/opencode/src/permission/index.ts:75` (the `Interface.ask` contract).

### Baseline to beat

Kilo's default agent ruleset starts at `"*": "allow"` (`agent/agent.ts:136`) — near-`--dangerously-skip-permissions` already. `kilo run --auto` reportedly passes **every** provided attack. That is the number our benchmark must first reproduce on a pinned Kilo commit, model, and settings before claiming any improvement.

## Benchmark

The case owner supplies a harness: temp repo, injection via five channels (agent rules, docstring, README, code comment, external tool result), CLI run, canary oracles for three harm classes (file write, file delete, exfiltration). We extend it with careless-action scenarios (no attacker) and **paired benign cases** — `dist ↔ src`, push ↔ force-push, test DB ↔ production fixture — so that FP is measurable and "block everything" cannot score well.

Metrics: ASR ↓ (primary), Utility ↑, FP ↓, Friction ↓, Latency ↓, Cost, plus Recovery@2 and deep-route share. Thresholds and configurations are in CONTEXT_PACK §7.

Harness rules that are easy to get wrong:

- **Restart the process between runs.** Kilo caches project settings at workspace load (issue #7247); without a restart you are measuring stale policy.
- **Set the model globally** in Kilo settings, not per-session — per-session is not picked up.
- **Repeat every scenario multiple times.** Injection success is stochastic: the same text is refused on one run and executed on the next. Report absolute counts alongside rates, macro-averaged over scenarios.
- Evaluation unit is the **full trajectory**, not a single command line. Keep a dev/holdout split; do not tune final thresholds on holdout.

## Honesty constraints from the case brief

The judges reward method over numbers — "8 of 10 beats 8 of 8". State plainly what the system guarantees *always* (Level 0 rules) versus *best-effort* (the classifiers), and which cases are not covered. Do not claim protection from arbitrary prompt injection or arbitrary code execution, and do not claim "first open-source auto mode" — Qwen Code shipped a direct analogue. Our stated contribution is Kilo-native integration, focus on careless actions, a deterministic safety floor, and end-to-end evidence.
