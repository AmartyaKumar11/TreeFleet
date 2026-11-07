# TreeFleet evaluation

E2 wall time min 249.6s, max 515.2s.

| Scenario | Run | Guard | Tasks | Wall | Fleet wall | Review | Cost | Planner | Expectation | Isolation |
|---|---|---|---|---|---|---|---|---|---|---|
| E1 | E1#1 | allow | 1 | 75.2s | 51.5s | 1.7s | $0.0248 | deepseek/deepseek-flash | pass | PASS |
| E2 | E2#1 | allow | 3 | 249.6s | 192.6s | 55.6s | $0.1186 | deepseek/deepseek-v4-pro | pass | PASS |
| E2 | E2#2 | allow | 3 | 515.2s | 212.6s | 99.0s | $0.1897 | deepseek/deepseek-v4-pro | pass | PASS |
| E3 | E3#1 | allow | 3 | 293.8s | 161.0s | 26.5s | $0.1228 | deepseek/deepseek-v4-pro | pass | PASS (1 contained violation) |
| E4 | E4#1 | block | 0 | 0.0s | 0.0s | 0.0s | n/a |  | pass |  |
| E5 | E5#1 | plan_rejected | 0 | 0.0s | 0.0s | 0.0s | n/a | forced | pass |  |
| E6 | E6-auto#1 | allow | 1 | 53.7s | 44.3s | 2.0s | $0.0014 | deepseek/deepseek-v4-pro | pass | PASS |
| E6 | E6-router#1 | allow | 1 | 60.5s | 46.8s | 1.9s | $0.0358 | deepseek/deepseek-v4-pro | pass | PASS |
| E7 | E7#1 | allow | 2 | 636.4s | 605.4s | 45.8s | 27483 tokens | forced | fail: statuses ['done', 'failed']; verdicts ['reject', 'reject'] | PASS |

## Variance

| Stage | E2#1 | E2#2 |
|---|---|---|
| scan | 0.0s | 0.0s |
| baseline | 2.5s | 2.4s |
| planner | 18.4s | 23.2s |
| fleet | 192.6s | 212.6s |
| review | 55.6s | 99.0s |
| revise | 0.0s | 185.8s |
| integrate | 2.2s | 2.4s |

The largest stage gap is revise: 186s. E2#2 spent 186s in a revise round on add-admin-tests. E2#1 spent 0s there. That revise round is the extra time.

Failures were not retried.
- E7#1: fail: statuses ['done', 'failed']; verdicts ['reject', 'reject']

## Tokens and Jev

- E1#1: cursor 12905, jev 1101, planner 1172, reviewer 413, worker usage not reported; Jev calls 3
- E2#1: cursor 95979, jev 3348, planner 2939, reviewer 12555, worker usage not reported; Jev calls 5
- E2#2: cursor 151967, jev 4178, planner 3526, reviewer 22636, worker usage not reported; Jev calls 6
- E3#1: cursor 130829, jev 2841, planner 2616, reviewer 6656, worker usage not reported; Jev calls 5
- E4#1: none; Jev calls 1
- E5#1: none; Jev calls 1
- E6-auto#1: cursor 14043, jev 736, planner 1008, reviewer 494, worker usage not reported; Jev calls 2
- E6-router#1: cursor 13572, jev 1227, planner 1526, reviewer 557, worker usage not reported; Jev calls 3
- E7#1: cursor usage not reported, jev 1581, planner 0, worker usage not reported; Jev calls 4

Worker usage not reported: CrewAI token_usage and the LiteLLM callback were empty. No worker token count was invented.

Main checkout was clean after every scenario.

## Estimates

This section is an extrapolation, not a measurement.
Formula: projected sequential = tasks * (measured sequential time / measured task count). Projected wall = ceil(tasks / 3) * that same per-task time. The cap 3 is workers.max_workers. Slots run together, so wall grows by batches of 3.
Average cost per task: $0.0389.

| Tasks | Projected wall | Projected sequential |
|---|---|---|
| 5 | 331.5s | 828.7s |
| 10 | 663.0s | 1657.4s |
| 20 | 1160.2s | 3314.8s |

