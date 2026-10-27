# Diagnosis of live run 71e5f9b6

No product code was changed before this note. Times are UTC. Process start is the worker log `CreationTime` converted from local +05:30. The agent card time is the `worker.spawned` event: the spawner returns only after `GET /.well-known/agent-card.json` is HTTP 200. There is no separate card event.

`git worktree list --porcelain` on `D:\pr-sentinel-demo` at diagnosis time:

```
worktree D:/pr-sentinel-demo
HEAD 3be485bd19c11a9daaa5f603a649b10c27c2cdd8
branch refs/heads/demo-base

worktree C:/wt/71e5f9b6/document-client-exported-types
HEAD 0899ea6bed3ba1f5a3a9f1d3461a2125030d52a9
branch refs/heads/tf/71e5f9b6/document-client-exported-types

worktree C:/wt/71e5f9b6/review-existing-test-patterns
HEAD 3be485bd19c11a9daaa5f603a649b10c27c2cdd8
branch refs/heads/tf/71e5f9b6/review-existing-test-patterns

worktree C:/wt/71e5f9b6/run-client-tests-and-check
HEAD 3be485bd19c11a9daaa5f603a649b10c27c2cdd8
branch refs/heads/tf/71e5f9b6/run-client-tests-and-check
```

Proof paths are stored with backslashes (`C:\wt\...`). Porcelain paths use forward slashes (`C:/wt/...`). Branch values in porcelain keep the `refs/heads/` prefix. `list_worktrees` strips that prefix before the proof compares branches. `_same` compares `normcase(resolve(...))`, so the slash difference is not a mismatch for the three worktrees that exist.

The task commit on `document-client-exported-types` is only `src/client/index.ts` and `src/client/types.ts` (`git show --stat HEAD`). The report summary lists 19 paths because `WorkerResult` diffs `main...HEAD`, and this repo's checked-out branch is `demo-base`, not `main`. The isolation proof diffs the run base `HEAD` (`3be485bd`) and correctly reports those two files in scope.

## Per task

| Task | Status | Error | Worktree path | `git worktree add` | In porcelain (exact strings) | Process start | Agent card | Last event | Phases |
|---|---|---|---|---|---|---|---|---|---|
| review-existing-test-patterns | failed | `no commit was made` | `C:\wt\71e5f9b6\review-existing-test-patterns` | yes | yes. proof `C:\wt\71e5f9b6\review-existing-test-patterns` vs porcelain `worktree C:/wt/71e5f9b6/review-existing-test-patterns`. proof branch `tf/71e5f9b6/review-existing-test-patterns` vs porcelain `branch refs/heads/tf/71e5f9b6/review-existing-test-patterns` | 12:20:55 (log created) | 12:21:02.156 (`worker.spawned`, pid 37276) | `worker.exited` 12:22:34.065 status failed, `a2a=remote` | card wait 7.2s. Cursor step 52.891s ending 12:22:23.840. Commit attempt 0.047s. Spawn to exit 91.9s |
| document-client-exported-types | done | none | `C:\wt\71e5f9b6\document-client-exported-types` | yes | yes. proof `C:\wt\71e5f9b6\document-client-exported-types` vs porcelain `worktree C:/wt/71e5f9b6/document-client-exported-types`. proof branch `tf/71e5f9b6/document-client-exported-types` vs porcelain `branch refs/heads/tf/71e5f9b6/document-client-exported-types` | 12:20:55 | 12:21:02.359 (pid 59332) | `worker.exited` 12:22:24.567 status done, `a2a=remote` | card wait 7.4s. Cursor step 66.672s ending 12:22:16.144. Commit 0.266s. Spawn to exit 82.2s |
| add-notifications-tests | failed | `upstream review-existing-test-patterns did not finish` | `C:\wt\71e5f9b6\add-notifications-tests` (never created) | no | no. porcelain has no line containing `add-notifications-tests`. proof still stored `C:\wt\71e5f9b6\add-notifications-tests` | none | none | `model_selected` 12:20:54.878. No `worker.spawned` | not started |
| add-admin-tests | failed | `upstream review-existing-test-patterns did not finish` | `C:\wt\71e5f9b6\add-admin-tests` (never created) | no | no. porcelain has no line containing `add-admin-tests` | none | none | `model_selected` 12:20:54.878. No `worker.spawned` | not started |
| run-client-tests-and-check | failed | `no commit was made` | `C:\wt\71e5f9b6\run-client-tests-and-check` | yes | yes. proof `C:\wt\71e5f9b6\run-client-tests-and-check` vs porcelain `worktree C:/wt/71e5f9b6/run-client-tests-and-check`. proof branch `tf/71e5f9b6/run-client-tests-and-check` vs porcelain `branch refs/heads/tf/71e5f9b6/run-client-tests-and-check` | 12:22:34 | 12:22:40.993 (pid 4856) | `worker.exited` 12:24:54.261 status failed, `a2a=remote` | card wait 7.0s. Cursor step 122.484s ending 12:24:48.475. Commit attempt 0.063s. Spawn to exit 133.3s |
| run-python-tests | failed | `upstream add-notifications-tests did not finish` | `C:\wt\71e5f9b6\run-python-tests` (never created) | no | no. porcelain has no line containing `run-python-tests` | none | none | `model_selected` 12:20:54.880. No `worker.spawned` | not started |

No `worker.step` has `timed_out: true`. No event mentions a timeout, a failed `git worktree add`, or a missing agent card. Configured limits were mcp startup 15s, card wait 40s, task timeout 180s. Measured card waits are about 7s. The longest cursor step is 122.5s.

## Root cause per task

`review-existing-test-patterns`. Cursor finished and edited nothing. The instructions say read and inspect. The worker then refuses a clean tree. Proving event, `events.jsonl` line 29:

`{"run_id":"71e5f9b6","seq":9,"ts":"2026-10-27T12:22:34.065420Z","source":"orchestrator","type":"worker.exited","task_id":"review-existing-test-patterns","payload":{"status":"failed","files":[],"a2a":"remote"}}`

The cursor step immediately before that (line 23) is `subtype: success`, `is_error: false`, duration 52.891s, and the result text is a review of test patterns, not a diff. `git status` in that worktree is empty and HEAD is still `3be485bd`. The report error is `no commit was made`.

`document-client-exported-types`. This task succeeded. Proving event, line 25:

`{"run_id":"71e5f9b6","seq":8,"ts":"2026-10-27T12:22:24.567679Z","source":"orchestrator","type":"worker.exited","task_id":"document-client-exported-types","payload":{"status":"done",...}}`

Its isolation row is all yes. The 19-file summary is the `main...HEAD` diff, not this commit. `git show --stat HEAD` is two in-scope files.

`add-notifications-tests` and `add-admin-tests`. Fleet treats a missing `head_sha` as "did not finish" and never calls `git worktree add`. Review failed with no sha, so both were skipped. There is no spawned event for either id after line 29. The report errors are `upstream review-existing-test-patterns did not finish`. Isolation then checks the path that was never created, so toplevel and registered are false, and `IsolationReport.ok` is false. Those two proof rows are not a path-comparison failure. The directories do not exist, and porcelain has no matching `worktree` line.

`run-client-tests-and-check`. Cursor finished, the shell was rejected, and no file changed. Proving event, line 38:

`{"run_id":"71e5f9b6","seq":11,"ts":"2026-10-27T12:24:54.261416Z","source":"orchestrator","type":"worker.exited","task_id":"run-client-tests-and-check","payload":{"status":"failed","files":[],"a2a":"remote"}}`

The cursor step (line 34) says `subtype: success`, `is_error: false`, duration 122.484s, and the result text says shell execution was rejected so `npm test` did not run. HEAD is still `3be485bd`. Same `no commit was made` failure.

`run-python-tests`. It depends on both add-tasks. Those never started, so this never started. No spawned event. The report error is `upstream add-notifications-tests did not finish`. Porcelain has no worktree line. The isolation row is the same missing-directory failure.

Wave 1 ran review and document together. Both cursor processes returned success. The run did not stall and did not time out.

## Hypothesis measurements

Scripts, not guesses. Scratch repos under `C:\tfh` were removed from the product path. Nothing in `src/` was changed for these runs.

1. Path comparison. For each live worktree the proof path and the porcelain line are not the same string. `review-existing-test-patterns`: proof `C:\wt\71e5f9b6\review-existing-test-patterns`, porcelain `worktree C:/wt/71e5f9b6/review-existing-test-patterns`. `_same` is true and `verify_task_isolation` returns `ok` true, `registered` true, `toplevel` true. The same holds for `document-client-exported-types` and `run-client-tests-and-check`. The three missing tasks have no porcelain line. A fresh `git worktree add` was given `c:\tfh\casewt\one` and git stored `C:/tfh/casewt/one`. Raw strings differ. `_same` is true and the proof is `ok`. `GetShortPathNameW` returned `C:/tfh/casewt/one`, the same stored path, so there is no 8.3 alias to mismatch. A junction at `C:\tfh\junction-one` reports toplevel `C:/tfh/casewt/one`, which `_same` matches. This is not the failure.

2. Concurrent cursor-agent. Two real processes, model `composer-2.5-fast`, throwaway worktrees `left` and `right`, started together. Left exit 0 in 32.984s and appended its sentence. Right exit 0 in 34.109s and appended its sentence. Wall 34.109s. Both had started before either finished. Neither stalled. The live run's two overlapping cursor steps also both returned success. A concurrency limit is not justified.

3. Timeouts with three workers. Parallel MCP handshakes on three worktrees: 1.078s, 1.078s, 1.078s, wall 1.078s, limit `mcp_startup_s` 15. Three real worker processes with `spikes/s9_fakes.py` started together (run `c3f7e104`). Log creation 12:47:11, 12:47:12, 12:47:12. Agent cards (`worker.spawned`) 12:47:17.558, 12:47:17.599, 12:47:17.690. Card wait about 6s, limit 40s. `start` steps 1.187s and 1.172s. Fake cursor steps 0.25s. Commits 0.203s. All three `worker.exited` by 12:47:21.727, status done. Task timeout is 180s. Nothing timed out.

4. Worktree creation. Four `create_worktree` calls at once finished in 0.172s. Porcelain listed all four (`alpha`, `beta`, `gamma`, `delta`). No error. This is not the failure.
