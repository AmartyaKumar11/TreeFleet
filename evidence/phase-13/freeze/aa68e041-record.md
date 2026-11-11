# Run record: aa68e041 (rehearsal-3)

Source: local `runs/aa68e041/` (read-only). Sanitized event slice: `aa68e041-events-excerpt.jsonl`.

## Identity

- **Run id:** `aa68e041`
- **Goal:** Add unit tests for src/notifications.py and, as a second task, add unit tests for src/admin.py
- **Stage (final):** `done`
- **Demo base SHA (`run.base_sha`):** `e92286b0ee8d722a9b28eb77e66cb89c6cf3bb43`
- **Freeze TreeFleet commit (tag `demo-freeze-1`):** `8b07d50464f7bbecc8d0e8e6ee3d6e3ac534d036`

## Per-task routing

| Task id | Tier (complexity) | Cursor model | Routing source |
|---|---|---|---|
| `add-notifications-unit-tests` | moderate | composer-2.5 | jev |
| `add-admin-unit-tests` | moderate | composer-2.5 | jev |

## Timeline (from events.jsonl)

Clock start (first event): `2026-11-10T11:53:00.900538Z`

| Milestone | Timestamp (UTC) | Elapsed (s) |
|---|---|---:|
| start | `2026-11-10T11:53:00.900538Z` | 0.000 |
| first_pr_opened PR#92 (add-admin-unit-tests) | `2026-11-10T11:58:44.219163Z` | 343.319 |
| pr_opened PR#93 (add-notifications-unit-tests) | `2026-11-10T11:58:50.495876Z` | 349.595 |
| checks_green PR#92 (add-admin-unit-tests) | `2026-11-10T11:59:09.814856Z` | 368.914 |
| checks_green PR#93 (add-notifications-unit-tests) | `2026-11-10T11:59:11.108262Z` | 370.208 |
| comment PR#93 attempt=1 (add-notifications-unit-tests) | `2026-11-10T12:02:37.858179Z` | 576.958 |
| revision_started PR#93 attempt=1 (add-notifications-unit-tests) | `2026-11-10T12:02:37.858179Z` | 576.958 |
| revision_pushed PR#93 attempt=1 (add-notifications-unit-tests) | `2026-11-10T12:03:15.776978Z` | 614.876 |
| checks_green PR#93 (add-notifications-unit-tests) | `2026-11-10T12:04:36.268964Z` | 695.368 |
| merged PR#93 (add-notifications-unit-tests) | `2026-11-10T12:05:11.140183Z` | 730.240 |
| merged PR#92 (add-admin-unit-tests) | `2026-11-10T12:05:26.866415Z` | 745.966 |
| done (run_done) | `2026-11-10T12:05:26.972065Z` | 746.072 |

### README cross-check

- First `pr.opened`: **343.3 s** — confirms README cell **343.3 s** (measured 343.319).
- Both initial `pr.checks_green`: **370.2 s** — README cell was `(see events)`; correct value is **370.2 s**.
- Both `pr.merged` / done: **746.0 s** wall (metrics.wall_seconds 746.515) — confirms README **746.0 s**.

## Attempts and reactions

### `add-notifications-unit-tests`

- WorkerResult attempts recorded: [1]
- Final PR: #93 state=`merged` checks=`passing` open=False
- reaction `approved_green` → `ignore` attempt=1 at +319.6s

### `add-admin-unit-tests`

- WorkerResult attempts recorded: [1]
- Final PR: #92 state=`merged` checks=`passing` open=False
- reaction `approved_green` → `ignore` attempt=1 at +335.6s

### Run-level reactions

- reaction `run_done` → `notify` attempt=1 at +746.1s

## Pull requests (final)

| Task | PR # | Final state | Checks |
|---|---:|---|---|
| `add-notifications-unit-tests` | 93 | merged | passing |
| `add-admin-unit-tests` | 92 | merged | passing |

## Cost (`report.json` → `metrics.costs`)

- **Recorded total USD:** 0.114587
- Fleet wall seconds: 262.32799999999406
- Wall seconds: 746.5149999999994

| Component | Model | USD | Label |
|---|---|---:|---|
| planner | deepseek/deepseek-flash | 0.000659 |  |
| worker | deepseek/deepseek-flash | 0.0 | usage not reported |
| cursor | composer-2.5 | 0.046311 | add-notifications-unit-tests r1 estimate |
| cursor | composer-2.5 | 0.017846 | add-notifications-unit-tests r1 estimate |
| cursor | composer-2.5 | 0.037758 | add-admin-unit-tests r1 estimate |
| cursor | composer-2.5 | 0.008697 | add-admin-unit-tests r1 estimate |
| reviewer | deepseek/deepseek-flash | 0.000935 | add-notifications-unit-tests r1 |
| reviewer | deepseek/deepseek-flash | 0.002275 | add-admin-unit-tests r1 |
| jev | jev-latest | 0.000106 | calls: 4 |

Worker-brain DeepSeek tokens are not recorded in report.json, so these totals understate real spend.

## Excerpt secret scan

- PASS: no matches for Authorization, api_key, Bearer tokens, sk-/ghp_/gho_/github_pat_/slack/google key prefixes.
- Excerpt lines: 33
- Written: `evidence/phase-13/freeze/aa68e041-events-excerpt.jsonl`

Record generated: 2026-11-11T02:50:20Z
