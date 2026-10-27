# TreeFleet run 1d4d116d

**Goal:** Add unit tests for src/notifications.py and, as a second task, add unit tests for src/admin.py.

## Tasks

| Task | Status | Verdict | Revise rounds | Branch | Files | Checks |
|---|---|---|---|---|---|---|
| notifications-unit-tests | done | approve | 0 | `tf/1d4d116d/notifications-unit-tests` | 1 | pytest:pass |
| admin-unit-tests | failed | reject | 1 | `tf/1d4d116d/admin-unit-tests` | 0 | pytest:fail |

## Models

- notifications-unit-tests: composer-2.5 (jev)
- admin-unit-tests: composer-2.5 (jev)

## Isolation proof

**Overall:** PASS

| Task | Process cwd | Toplevel | Registered | Canary absent | Within scope |
|---|---|---|---|---|---|
| notifications-unit-tests | yes | yes | yes | yes | yes |
| admin-unit-tests | yes | yes | yes | yes | yes |

Main checkout unchanged: yes; canary still present: yes

## Integration

Branch: `tf/1d4d116d/integrated`
Merged: notifications-unit-tests
Excluded:
- admin-unit-tests: rejected: no new diff
Post-merge checks: pytest:pass, npm-test:pass, test:pass
Merge into `demo-base`: `git -C D:\pr-sentinel-demo merge tf/1d4d116d/integrated`

## Notes

- pip-install-pytest is setup (python -m pip install pytest) and was not run
- worker usage not reported: CrewAI token_usage and the LiteLLM callback were empty

## Cost

| Role | Model | In | Out | Cache read | Cache write | USD | Note |
|---|---|---|---|---|---|---|---|
| planner | deepseek/deepseek-flash | 551 | 990 | 0 | 0 | 0.000677 |  |
| worker | deepseek/deepseek-flash | 0 | 0 | 0 | 0 | 0.000000 | usage not reported |
| cursor | composer-2.5 | 17370 | 2042 | 61690 | 0 | 0.017443 | notifications-unit-tests r1 estimate |
| cursor | composer-2.5 | 18976 | 2762 | 62883 | 0 | 0.019482 | notifications-unit-tests r1 estimate |
| cursor | composer-2.5 | 18945 | 3231 | 129146 | 0 | 0.033907 | admin-unit-tests r1 estimate |
| cursor | composer-2.5 | 21301 | 3275 | 92301 | 0 | 0.026648 | admin-unit-tests r1 estimate |
| cursor | composer-2.5 | 20730 | 4120 | 106433 | 0 | 0.031587 | admin-unit-tests r2 estimate |
| reviewer | deepseek/deepseek-flash | 1329 | 2708 | 0 | 0 | 0.001824 | notifications-unit-tests r1 |
| reviewer | deepseek/deepseek-flash | 1317 | 3136 | 0 | 0 | 0.002079 | admin-unit-tests r1 |
| jev | jev-latest | 2532 | 0 | 0 | 0 | 0.000106 | calls: 4 |

## Metrics

- Wall time: 343.4s (end-to-end clock, including planning)
- Sequential time: 514.3s (pre-fleet overhead plus task, review and integration time)
- Speedup: 1.5x (sequential time divided by wall time)
- Fleet wall: 223.0s (time while workers run)
- Fleet speedup: 2.04x (sum of task durations divided by fleet wall)
- Review time: 36.7s
- Integration time: 2.3s
- Total cost: $0.1338
- Jev calls: 4
