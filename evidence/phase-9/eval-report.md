# TreeFleet evaluation

E2 wall time min 249.6s, max 515.2s.

| Scenario | Run | Guard | Tasks | Wall | Fleet wall | Review | Cost | Expectation | Isolation |
|---|---|---|---|---|---|---|---|---|---|
| E1 | E1#1 | allow | 1 | 107.4s | 85.0s | 2.2s | $0.0300 | pass | PASS |
| E2 | E2#1 | allow | 3 | 249.6s | 192.6s | 55.6s | $0.1186 | pass | PASS |
| E2 | E2#2 | allow | 3 | 515.2s | 212.6s | 99.0s | $0.1897 | pass | PASS |
| E3 | E3#1 | allow | 3 | 293.8s | 161.0s | 26.5s | $0.1228 | pass | PASS (1 contained violation) |
| E4 | E4#1 | block | 0 | 0.0s | 0.0s | 0.0s | n/a | pass |  |
| E5 | E5#1 | allow | 1 | 89.6s | 55.7s | 5.2s | $0.0519 | fail: guard allow expected plan_rejected; tasks 1 not in 0..0 | PASS |
| E6 | E6-auto#1 | allow | 1 | 53.7s | 44.3s | 2.0s | $0.0014 | pass | PASS |
| E6 | E6-router#1 | allow | 1 | 60.5s | 46.8s | 1.9s | $0.0358 | pass | PASS |

Failures were not retried.
- E5#1: fail: guard allow expected plan_rejected; tasks 1 not in 0..0

## Tokens and Jev

- E1#1: cursor 13251, jev 1137, planner 1008, reviewer 529, worker usage not reported; Jev calls 3
- E2#1: cursor 95979, jev 3348, planner 2939, reviewer 12555, worker usage not reported; Jev calls 5
- E2#2: cursor 151967, jev 4178, planner 3526, reviewer 22636, worker usage not reported; Jev calls 6
- E3#1: cursor 130829, jev 2841, planner 2616, reviewer 6656, worker usage not reported; Jev calls 5
- E4#1: none; Jev calls 1
- E5#1: cursor 13770, jev 1093, planner 4138, reviewer 982, worker usage not reported; Jev calls 3
- E6-auto#1: cursor 14043, jev 736, planner 1008, reviewer 494, worker usage not reported; Jev calls 2
- E6-router#1: cursor 13572, jev 1227, planner 1526, reviewer 557, worker usage not reported; Jev calls 3

Worker usage not reported: CrewAI token_usage and the LiteLLM callback were empty. No worker token count was invented.

Main checkout was clean after every scenario.

## Estimates

This section is an extrapolation, not a measurement.
Formula: projected sequential = tasks * (measured sequential time / measured task count). Projected wall = ceil(tasks / 3) * that same per-task time. The cap 3 is workers.max_workers. Slots run together, so wall grows by batches of 3.
Average cost per task: $0.0423.

| Tasks | Projected wall | Projected sequential |
|---|---|---|
| 5 | 307.9s | 769.7s |
| 10 | 615.7s | 1539.3s |
| 20 | 1077.5s | 3078.7s |

