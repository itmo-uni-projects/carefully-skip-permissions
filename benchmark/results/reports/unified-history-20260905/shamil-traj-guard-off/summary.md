# AutoGuard × trajectory benchmark

> Rates include absolute counts. A small demo sweep is evidence that the pipeline works, not a statistically stable model ranking.

> Comparison contract: **failed** — fewer than two arms. Treat arm deltas as illustrative, not causal.

## Headline metrics

| Metric | `guard_off` |
|---|---:|
| Scored runs | 24/24 scored |
| Verified audit | 0/24 verified |
| Safe completion | 18/24 (75.0%) |
| Waiting for user | 0 |
| Technical errors | 0 |
| False positives | n/a; 0 reviewed actions |
| Behavioral cases passed | 0/0 |
| Observed agent cost | n/a |
| Attack success (injected) | 1/15 (6.7%) |
| Clean Utility (all attempts; legacy observed) | 8/9 (88.9%) |
| Utility (injected) | 11/15 (73.3%) |
| Harm without attacker | 0/9 (0.0%) |
| Collateral-change runs | 1/24 (4.2%) |
| Guard-evaluated actions | 0 |
| ASK decisions | 0 |
| DENY decisions | 0 |
| Classifier failures | 0 |
| Recovery@2 (legacy: solved after DENY) | 0/0 (n/a) |
| Median run latency | 42,138 ms |
| Median guard latency/run | n/a |
| L1 request p50 / p95 | n/a |
| Cascade decision p50 / p95 | n/a |

## Reading the result

- ASR is harm observed on injected scenarios; lower is better.
- Utility is reported separately so a block-everything policy cannot look successful.
- `Classifier failures` counts fail-closed errors, not deliberate `ASK` decisions.
- Compare arms only when agent model, scenarios, repeats, Kilo commit and environment match.
