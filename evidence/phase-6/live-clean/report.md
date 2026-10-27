# TreeFleet run d89593f5

**Goal:** Add unit tests for the notifications module and the admin module, and document the exported types in the TypeScript client.

## Tasks

| Task | Status | Verdict | Revise rounds | Branch | Files | Checks |
|---|---|---|---|---|---|---|
| notifications-tests | done | approve | 1 | `tf/d89593f5/notifications-tests` | 1 | pytest:pass |
| admin-tests | done | approve | 0 | `tf/d89593f5/admin-tests` | 1 | pytest:pass |
| client-type-docs | done | approve | 0 | `tf/d89593f5/client-type-docs` | 1 | npm-test:pass, test:pass |

## Models

- notifications-tests: composer-2.5 (jev)
- admin-tests: composer-2.5 (jev)
- client-type-docs: composer-2.5 (jev)

## Isolation proof

**Overall:** PASS

| Task | Process cwd | Toplevel | Registered | Canary absent | Within scope |
|---|---|---|---|---|---|
| notifications-tests | yes | yes | yes | yes | yes |
| admin-tests | yes | yes | yes | yes | yes |
| client-type-docs | yes | yes | yes | yes | yes |

Main checkout unchanged: yes; canary still present: yes

## Integration

Branch: `tf/d89593f5/integrated`
Merged: admin-tests, client-type-docs, notifications-tests
Post-merge checks: pytest:pass, npm-test:pass, test:pass
Merge into `demo-base`: `git -C D:\pr-sentinel-demo merge tf/d89593f5/integrated`

## Notes

- warning: repo tracks generated files: src/__pycache__/__init__.cpython-312.pyc, src/__pycache__/auth.cpython-312.pyc, src/__pycache__/notifications.cpython-312.pyc, src/__pycache__/orders.cpython-312.pyc, src/__pycache__/users.cpython-312.pyc, tests/__pycache__/test_auth.cpython-312-pytest-9.1.1.pyc
- pip-install-pytest is setup (python -m pip install pytest) and was not run
- baseline checks 2.219s

## Cost

| Role | Model | In | Out | Cache read | Cache write | USD | Note |
|---|---|---|---|---|---|---|---|
| planner | deepseek/deepseek-v4-pro | 603 | 1939 | 0 | 0 | 0.004237 |  |
| worker | deepseek/deepseek-flash | 0 | 0 | 0 | 0 | 0.000000 | usage not reported |
| cursor | composer-2.5 | 36068 | 5197 | 125143 | 0 | 0.038021 | estimate |
| cursor | composer-2.5 | 31043 | 3632 | 99125 | 0 | 0.028905 | estimate |
| cursor | composer-2.5 | 13535 | 1214 | 54417 | 0 | 0.013918 | estimate |
| jev | jev-latest | 3932 | 0 | 0 | 0 | 0.000165 | calls: 6 |

## Metrics

- Wall time: 434.7s (end-to-end clock, including planning)
- Sequential time: 407.5s (pre-fleet overhead plus the sum of task durations)
- Speedup: 0.94x (sequential time divided by wall time)
- Fleet wall: 170.5s (time while workers run)
- Fleet speedup: 2.23x (sum of task durations divided by fleet wall)
- Total cost: $0.0852
- Jev calls: 6
