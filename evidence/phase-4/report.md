# TreeFleet run 71e5f9b6

**Goal:** Add unit tests for the notifications module and the admin module, and document the exported types in the TypeScript client.

## Tasks

| Task | Status | Verdict | Branch | Files | Checks |
|---|---|---|---|---|---|
| review-existing-test-patterns | failed | n/a | `tf/71e5f9b6/review-existing-test-patterns` | 0 | none |
| document-client-exported-types | done | n/a | `tf/71e5f9b6/document-client-exported-types` | 19 | none |
| add-notifications-tests | failed | n/a | `tf/71e5f9b6/add-notifications-tests` | 0 | none |
| add-admin-tests | failed | n/a | `tf/71e5f9b6/add-admin-tests` | 0 | none |
| run-client-tests-and-check | failed | n/a | `tf/71e5f9b6/run-client-tests-and-check` | 0 | none |
| run-python-tests | failed | n/a | `tf/71e5f9b6/run-python-tests` | 0 | none |

## Models

- review-existing-test-patterns: composer-2.5 (router)
- add-notifications-tests: composer-2.5 (router)
- add-admin-tests: composer-2.5 (router)
- document-client-exported-types: composer-2.5 (router)
- run-python-tests: composer-2.5 (router)
- run-client-tests-and-check: composer-2.5 (router)

## Isolation proof

**Overall:** FAIL

| Task | Process cwd | Toplevel | Registered | Canary absent | Within scope |
|---|---|---|---|---|---|
| review-existing-test-patterns | yes | yes | yes | yes | yes |
| document-client-exported-types | yes | yes | yes | yes | yes |
| add-notifications-tests | yes | NO | NO | yes | yes |
| add-admin-tests | yes | NO | NO | yes | yes |
| run-client-tests-and-check | yes | yes | yes | yes | yes |
| run-python-tests | yes | NO | NO | yes | yes |

Main checkout unchanged: yes; canary still present: yes

## Metrics

- Wall time: 344.4s
- Sequential estimate: 307.0s
- Speedup: 0.89x
- Total cost: $0.0000
- Jev calls: 0
