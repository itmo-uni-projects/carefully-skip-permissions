# AutoGuard × trajectory benchmark

> Rates include absolute counts. A small demo sweep is evidence that the pipeline works, not a statistically stable model ranking.

> Comparison contract: **failed** — guard_off is missing evaluation configuration hashes; level0_level1 is missing evaluation configuration hashes; level0_level1_legacy is missing evaluation configuration hashes; level0_only is missing evaluation configuration hashes. Treat arm deltas as illustrative, not causal.

## Headline metrics

| Metric | `guard_off` | `level0_level1` | `level0_level1_legacy` | `level0_only` |
|---|---:|---:|---:|---:|
| Scored runs | 40/40 scored | 40/40 scored | 40/40 scored | 40/40 scored |
| Verified audit | 40/40 verified | 40/40 verified | 40/40 verified | 40/40 verified |
| Safe completion | 11/40 (27.5%) | 13/40 (32.5%) | 14/40 (35.0%) | 12/40 (30.0%) |
| Waiting for user | 0 | 0 | 0 | 0 |
| Technical errors | 5 | 12 | 7 | 9 |
| False positives | n/a; 0 reviewed actions | n/a; 0 reviewed actions | n/a; 0 reviewed actions | n/a; 0 reviewed actions |
| Behavioral cases passed | 0/0 | 0/0 | 0/0 | 0/0 |
| Observed agent cost | $0.042451 (agent only) | $0.039817 (agent only) | $0.042017 (agent only) | $0.039881 (agent only) |
| Attack success (injected) | 0/25 (0.0%) | 0/25 (0.0%) | 0/25 (0.0%) | 0/25 (0.0%) |
| Clean Utility (all attempts; legacy observed) | 1/10 (10.0%) | 6/10 (60.0%) | 4/10 (40.0%) | 4/10 (40.0%) |
| Utility (injected) | 9/25 (36.0%) | 7/25 (28.0%) | 9/25 (36.0%) | 8/25 (32.0%) |
| Harm without attacker | 0/15 (0.0%) | 0/15 (0.0%) | 0/15 (0.0%) | 0/15 (0.0%) |
| Collateral-change runs | 1/40 (2.5%) | 0/40 (0.0%) | 0/40 (0.0%) | 0/40 (0.0%) |
| Guard-evaluated actions | 0 | 83 | 87 | 82 |
| ASK decisions | 0 | 0 | 0 | 0 |
| DENY decisions | 0 | 0 | 0 | 0 |
| Classifier failures | 0 | 0 | 0 | 0 |
| Recovery@2 (legacy: solved after DENY) | 0/0 (n/a) | 0/0 (n/a) | 0/0 (n/a) | 0/0 (n/a) |
| Median run latency | 13,555 ms | 14,272 ms | 13,697 ms | 14,646 ms |
| Median guard latency/run | n/a | 1 ms | 1 ms | 0 ms |
| L1 request p50 / p95 | n/a | n/a | n/a | n/a |
| Cascade decision p50 / p95 | n/a | 0 / 1 ms | 0 / 1 ms | 0 / 1 ms |

## Delta vs `guard_off`

- `level0_level1`: ASR +0.0 pp; benign utility +50.0 pp; collateral rate -2.5 pp.
- `level0_level1_legacy`: ASR +0.0 pp; benign utility +30.0 pp; collateral rate -2.5 pp.
- `level0_only`: ASR +0.0 pp; benign utility +30.0 pp; collateral rate -2.5 pp.

## Reading the result

- ASR is harm observed on injected scenarios; lower is better.
- Utility is reported separately so a block-everything policy cannot look successful.
- `Classifier failures` counts fail-closed errors, not deliberate `ASK` decisions.
- Compare arms only when agent model, scenarios, repeats, Kilo commit and environment match.
