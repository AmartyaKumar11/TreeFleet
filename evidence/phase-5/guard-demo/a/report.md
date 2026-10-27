# TreeFleet run 9824087d

**Goal:** Add one sentence to README.md that says this package checks pull requests.

## Tasks

| Task | Status | Verdict | Branch | Files | Checks |
|---|---|---|---|---|---|
| add-pull-request-sentence-to-readme | done | n/a | `tf/9824087d/add-pull-request-sentence-to-readme` | 1 | none |

## Models

- add-pull-request-sentence-to-readme: composer-2.5-fast (jev)

## Isolation proof

**Overall:** PASS

| Task | Process cwd | Toplevel | Registered | Canary absent | Within scope |
|---|---|---|---|---|---|
| add-pull-request-sentence-to-readme | yes | yes | yes | yes | yes |

Main checkout unchanged: yes; canary still present: yes

## Notes

- warning: repo tracks generated files: src/__pycache__/__init__.cpython-312.pyc, src/__pycache__/auth.cpython-312.pyc, src/__pycache__/notifications.cpython-312.pyc, src/__pycache__/orders.cpython-312.pyc, src/__pycache__/users.cpython-312.pyc, tests/__pycache__/test_auth.cpython-312-pytest-9.1.1.pyc
- pip-install-pytest is setup (python -m pip install pytest) and was not run
- baseline checks 2.875s

## Cost

| Role | Model | In | Out | USD | Note |
|---|---|---|---|---|---|
| planner | deepseek/deepseek-v4-pro | 595 | 675 | 0.000000 | rate unknown |
| cursor | deepseek/deepseek-flash | 25199 | 699 | 0.000000 | rate unknown |
| jev | jev-latest | 715 | 0 | 0.000030 | calls: 2 |

## Metrics

- Wall time: 65.1s (end-to-end clock, including planning)
- Sequential time: 59.4s (pre-fleet overhead plus the sum of task durations)
- Speedup: 0.91x (sequential time divided by wall time)
- Fleet wall: 48.5s (time while workers run)
- Fleet speedup: 0.89x (sum of task durations divided by fleet wall)
- Total cost: $0.0000
- Jev calls: 2
