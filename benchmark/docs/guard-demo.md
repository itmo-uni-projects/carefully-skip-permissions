# AutoGuard × benchmark: demo runbook

This runbook connects the AutoGuard implementation in the Kilo fork to the
injection trajectory suite and produces one controlled `guard_off` versus
`level0_level1` comparison. The entrypoint is
`scripts/run_guard_demo.py`; it delegates to the existing trajectory runner,
validator and scorer instead of defining a second metric path.

## What is pinned

`guard-demo.lock.json` records both open Kilo pull requests and pins the
checkout used by the demo:

- [PR #1](https://github.com/itmo-uni-projects/kilocode/pull/1) — Level 0 +
  Level 1 cascade;
- [PR #2](https://github.com/itmo-uni-projects/kilocode/pull/2) — authority
  extraction, utility rules and the file plugin that emits
  `.autoguard-actions.jsonl`;
- demo commit `5fb96088a8e409cda93b592eda4324cb546d3a27`.

Both PRs are still review branches. PR #2 targets the Level 2 feature branch,
not Kilo `main`, so its head is the reproducible integration checkout for this
demo; it is not a claim that the implementation is production-ready.

## One-time setup

```bash
git clone https://github.com/itmo-uni-projects/kilocode.git ../kilocode
git -C ../kilocode fetch origin \
  feat/autoguard-utility-rules:feat/autoguard-utility-rules
git -C ../kilocode switch --detach \
  5fb96088a8e409cda93b592eda4324cb546d3a27

cd ../kilocode
bun install
```

Kilo uses its normal global provider authentication for the agent model. The
Level 1 classifier is configured independently through an OpenAI-compatible
endpoint:

```bash
export AUTOGUARD_L1_BASE_URL="http://127.0.0.1:1234/v1"
export AUTOGUARD_L1_MODEL="Qwen3.5-9B"
# Only when that endpoint requires it:
export AUTOGUARD_L1_API_KEY="..."
```

Do not set `KILO_CONFIG` yourself for this command. The demo runner generates
an isolated config containing the pinned `bench-plugin.ts` for guarded runs
and omits it for the baseline. It also rejects an already-set `KILO_CONFIG`
instead of silently contaminating `guard_off`.

## Preflight without a model call

```bash
cd carefully-skip-permissions/benchmark
uv sync

uv run python scripts/run_guard_demo.py \
  --kilo-repo /absolute/path/to/kilocode \
  --agent-model openrouter/openai/gpt-oss-120b \
  --plan-only
```

The plan prints the exact Kilo/Guard SHA, agent command, arms and any missing
Level 1 environment variables. It creates no config, workspace or model
request.

## Short rehearsal

This paired smoke uses one benign control and its injected counterpart. It is
good for checking the stage wiring, but one injected observation is not an ASR
estimate.

```bash
uv run python scripts/run_guard_demo.py \
  --kilo-repo /absolute/path/to/kilocode \
  --agent-model openrouter/openai/gpt-oss-120b \
  --scenario-id traj-build-cleanup-clean \
  --scenario-id traj-build-cleanup-comment-wipe \
  --repeats 1 \
  --acknowledge-no-os-sandbox
```

## Full comparison

```bash
uv run python scripts/run_guard_demo.py \
  --kilo-repo /absolute/path/to/kilocode \
  --agent-model openrouter/openai/gpt-oss-120b \
  --repeats 3 \
  --acknowledge-no-os-sandbox
```

The default timeout is 900 seconds per trial because the first file-plugin
load can install `@kilocode/plugin`. Every later agent trial still starts a
fresh Kilo process: reusing one would measure cached project policy.

## Output

Each run creates a gitignored directory under
`results/raw/guard-demo-<UTC timestamp>/`:

| File | Purpose |
|---|---|
| `manifest.json` | exact benchmark/Kilo SHAs, models, arms and command |
| `guard_off.jsonl` | baseline trial records |
| `level0_level1.jsonl` | guarded trial records and per-action decisions |
| `scores.json` | machine-readable ASR, utility, harm, collateral, friction, recovery and latency |
| `summary.md` | demo-ready comparison table with absolute counts |
| `runtime/autoguard.json` | generated file-plugin config; local only |

The scorer also checks a comparison contract. Deltas are marked controlled
only when arms use the same Kilo commit, agent model, temperature, seed, OS and
scenario/repeat coverage. The earlier committed experiment used Kilo `v7.5.9`
for `guard_off` and a fork checkout for the guarded arm, so its numbers remain
useful historical evidence but its arm deltas are now explicitly marked
illustrative rather than causal.

## Safety boundary

Disposable git workspaces protect the source fixtures and make state diffs
reproducible; they are not an OS sandbox. Run the live command in a disposable
container or VM without unrelated secrets. `--acknowledge-no-os-sandbox` is
required so a demo cannot accidentally imply process containment.
