# TreeFleet run 55d0986a

**Goal:** Add unit tests for the notifications module and the admin module, and document the exported types in the TypeScript client.

## Tasks

| Task | Status | Verdict | Branch | Files | Checks |
|---|---|---|---|---|---|
| add-notifications-tests | done | n/a | `tf/55d0986a/add-notifications-tests` | 9 | pytest:pass |
| add-admin-tests | done | n/a | `tf/55d0986a/add-admin-tests` | 9 | pytest:pass |
| document-ts-exported-types | done | n/a | `tf/55d0986a/document-ts-exported-types` | 1 | npm-test:pass, test:pass |

## Models

- add-notifications-tests: composer-2.5 (router)
- add-admin-tests: composer-2.5 (router)
- document-ts-exported-types: composer-2.5 (router)

## Isolation proof

**Overall:** PASS

| Task | Process cwd | Toplevel | Registered | Canary absent | Within scope |
|---|---|---|---|---|---|
| add-notifications-tests | yes | yes | yes | yes | yes |
| add-admin-tests | yes | yes | yes | yes | yes |
| document-ts-exported-types | yes | yes | yes | yes | yes |

Main checkout unchanged: yes; canary still present: yes

## Notes

- pip-install-pytest is setup (python -m pip install pytest) and was not run
- baseline checks 2.671s

## Metrics

- Wall time: 126.2s (end-to-end clock, including planning)
- Sequential time: 236.0s (pre-fleet overhead plus the sum of task durations)
- Speedup: 1.87x (sequential time divided by wall time)
- Fleet wall: 79.3s (time while workers run)
- Fleet speedup: 2.4x (sum of task durations divided by fleet wall)
- Total cost: $0.0000
- Jev calls: 0
