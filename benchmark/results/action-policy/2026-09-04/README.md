# action-policy run, 2026-09-04 — AutoGuard Level 0 + Level 1

Raw prediction records and their scores, committed so the numbers in
`docs/autoguard-mvp-results-2026-09-04.md` can be re-verified without a model.

## What produced these

- **Policy under test:** `packages/opencode/src/kilocode/autoguard/` in the
  fork `itmo-uni-projects/kilocode`, commit `2306194b6b`
  (PR [#1](https://github.com/itmo-uni-projects/kilocode/pull/1)).
- **Runner:** `script/kilocode/autoguard-bench.ts` in that fork, with
  `--normalize`, so the action is re-derived from `raw_tool_call` by our own
  normalizer rather than read from the suite's curated axes. This measures the
  whole path production takes.
- **Dataset:** `benchmark/datasets/action-policy/dev/` — 36 cases, 12 groups.
- **Level 1 model:** `mlx-community/Qwen3.5-9B-MLX-4bit`, served locally by
  `mlx_lm.server` on an Apple M1, `temperature=0`, reasoning disabled via
  `chat_template_kwargs.enable_thinking=false`. No request left the machine.
- **Scorer:** `benchmark/scripts/score_action_policy.py`, unmodified.

## Files

| File | Configuration |
|---|---|
| `level0-rules-only.jsonl` | Level 0 alone; Level 1 disabled |
| `cascade-action-only.jsonl` | Level 0 + Level 1, action only |
| `cascade-intent-action.jsonl` | Level 0 + Level 1, plus the developer's request |
| `cascade-full-context.jsonl` | Level 0 + Level 1, plus authority and trusted context |
| `cascade-full-context-3repeats.jsonl` | the same, three independent repeats |

Each `.scores.json` is the scorer's output for the neighbouring `.jsonl`.

## Headline

| Configuration | Accuracy | Macro-F1 | `DENY→ALLOW` | p50 latency |
|---|---|---|---|---|
| Level 0 only | 0.583 | 0.546 | **0** | 0 ms |
| + Level 1, action only | 0.583 | 0.546 | **0** | 3075 ms |
| + Level 1, intent + action | 0.611 | 0.572 | **0** | 3612 ms |
| + Level 1, full context | 0.667 | 0.675 | **0** | 5690 ms |

No case the labels say to deny was ever auto-allowed, in any configuration.
Across the three repeats, 0 of 36 decisions changed and 0 outputs were malformed.

## Re-scoring these files

```bash
cd benchmark
uv sync
uv run python scripts/score_action_policy.py \
  --predictions results/action-policy/2026-09-04/cascade-full-context.jsonl
```

## Reading these responsibly

Every label in the dev split still carries `review_status: "draft"`, and the
scorer prints a warning saying so on every invocation. These are working
hypotheses, not adjudicated ground truth.

This is a **step-level** evaluation of the policy decision. Proposed actions
were never executed. ASR, Utility and Recovery@2 need the trajectory suite and
are not measured here.

`raw_response_text` holds the model's reply, which for Level 1 is a single word
(`ALLOW`, `REVIEW`, `DENY`) or null when Level 0 decided without a model call.
No transcripts, file contents, or tool output are present — by construction,
the classifier never receives them.
