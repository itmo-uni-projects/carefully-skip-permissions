# AutoGuard × trajectory benchmark

> Rates include absolute counts. A small demo sweep is evidence that the pipeline works, not a statistically stable model ranking.

> Comparison contract: **failed** — kilo_commit differs across arms. Treat arm deltas as illustrative, not causal.

## Headline metrics

| Metric | `guard_off` | `level0_level1` |
|---|---:|---:|
| Scored runs | 24/24 scored | 24/24 scored |
| Attack success (injected) | 1/15 (6.7%) | 0/15 (0.0%) |
| Utility (benign) | 8/9 (88.9%) | 4/9 (44.4%) |
| Utility (injected) | 11/15 (73.3%) | 6/15 (40.0%) |
| Harm without attacker | 0/9 (0.0%) | 0/9 (0.0%) |
| Collateral-change runs | 1/24 (4.2%) | 0/24 (0.0%) |
| Guard-evaluated actions | 0 | 112 |
| ASK decisions | 0 | 8 |
| DENY decisions | 0 | 3 |
| Classifier failures | 0 | 0 |
| Solved after DENY | 0/0 (n/a) | 0/2 (0.0%) |
| Median run latency | 42,138 ms | 30,294 ms |
| Median guard latency/run | n/a | 2 ms |

## Delta vs `guard_off`

- `level0_level1`: ASR -6.7 pp; benign utility -44.4 pp; collateral rate -4.2 pp.

## Reading the result

- ASR is harm observed on injected scenarios; lower is better.
- Utility is reported separately so a block-everything policy cannot look successful.
- `Classifier failures` counts fail-closed errors, not deliberate `ASK` decisions.
- Compare arms only when agent model, scenarios, repeats, Kilo commit and environment match.
