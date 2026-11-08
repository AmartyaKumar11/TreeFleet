# S15: session lifecycle

Report only. No TreeFleet source changes. Stub fleet: `OfflineFakes` with `spikes/s9_fakes.py`, `TREEFLEET_MOCK_LLM=1`, no Cursor, no DeepSeek, no network. Worktrees under `C:\wt\<run_id>\<task>`. Runs under a temp `runs/` directory. The orchestrator was a child process so it could be killed from outside.

`RunId` is `^[a-z0-9]{6,12}$`. The runs below are `s15aa01` (hard kill), `s15aa0b` (second run on the same repo), `s15aa02` (one worker killed), `s15aa03` (console break), `s15aa04` (hard kill of the real interpreter, not the venv launcher).

## A. Persistence

There is no session database. The ADK session is `InMemorySessionService` in `orchestrator/pipeline.py:run_pipeline`. Plan, routing, worker results, verdicts, isolation, and the integration report live in that process. They are not written until `ReporterAgent` finishes, or until a blocked run writes a short report.

| Record | Path | When it is written | Enough to rebuild task status after a restart? |
|---|---|---|---|
| Event log | `runs/<run_id>/events.jsonl` | `events.py:EventSink._record`, append on every emit. The orchestrator and each worker open their own sink (`worker/crew.py:_sink` when `TREEFLEET_RUNS` is set). | No. A finished run's `worker.exited`, `review_verdict`, and `phase` lines describe the run. A killed run stops at `worker.spawned` plus whatever the workers append. Worker sinks start `seq` at 0 again, so the file has two sequences. There is no status field for the run. |
| Worker stdout | `runs/<run_id>/<task_id>.log` | `worker/spawner.py:spawn_worker` opens it with `"w"` before `Popen`. | No. It is the worker process log. |
| Report | `runs/<run_id>/report.json`, `report.md`, `timing.md` | `orchestrator/agents.py` reporter, after review and integrate. A blocked goal or a rejected plan writes only `report.md` and `report.json` from `pipeline.py:_write_blocked`. | Yes for a run that reached the reporter. Absent if the orchestrator dies during the fleet. |
| Plan | ADK `session.state["plan"]` only | Planner, in memory. Copied into `report.json` if the run finishes. | No separate plan file. |
| Worker result | ADK `session.state["worker_results"]` | `fleet.py` after each A2A reply. Copied into the report at the end. | No. The A2A result is not stored on disk. A commit on the task branch is the only durable task output. |
| Review | ADK `session.state["verdicts"]`, plus `review_started` / `review_verdict` events | `orchestrator/review.py:review_loop` | Only the events, and only if the orchestrator was still alive to write them. |
| Merge | Worktree `C:\wt\<run_id>\integrated`, branch `tf/<run_id>/integrated`, ADK `session.state["integration"]` | `git/merge.py:integrate` during the integrator. The report copies it at the end. | The integrated branch exists only if integrate ran. There is no merge-state file. |
| Worktree registry | `git worktree list` in the repo. Directory `C:\wt\<run_id>\<task_id>`, branch `tf/<run_id>/<task_id>` | `git/worktrees.py:create_worktree` when the task starts. | The registry says the worktree and branch exist. It does not say queued, running, failed, or killed. |
| Canary | `<repo>/.treefleet_canary` | `git/canary.py:write_canary` at fleet start. Listed in `.git/info/exclude`, so `git status` stays clean. | No. `cleanup_run` deletes it when the file contains that run id. |

`pipeline.py:run_pipeline` `finally` kills worker processes. It does not delete worktrees. `cleanup_run` deletes worktrees whose branch starts with `tf/<run_id>/` and leaves the branches.

## B. Process tree

Production starts every child with `subprocess.Popen`, `shell=False`, and no `creationflags`. There is no Windows Job Object and no `CREATE_NEW_PROCESS_GROUP` in `worker/spawner.py`, `cursor_run.py`, or `mcp_servers/worktree_fs_server.py`. `git/worktrees.py:kill_tree` walks `psutil` children and calls `kill` on each. That runs in the pipeline `finally`, on a failed spawn, and after a cursor call. It does not run if the orchestrator process is terminated from outside.

Observed mid-fleet (`s15aa01`, before the kill). The venv `python.exe` is a launcher. The real interpreter is its child.

| Process | Parent | How it is started |
|---|---|---|
| Orchestrator launcher | The process that started the run (CLI or this spike) | `python.exe` from `.venv-orch\Scripts` |
| Orchestrator interpreter | That launcher | Same command line, `cpython-3.12` `python.exe` |
| One worker launcher per task | Orchestrator interpreter | `spawn_worker`: `.venv-worker\Scripts\python.exe -m treefleet.worker.main` |
| Worker interpreter | Worker launcher | Same module, cwd = the task worktree |
| MCP filesystem server | Worker interpreter, only while a tool call or the startup handshake runs | `worktree_fs_server.py`: `Popen` of `-m treefleet.mcp_servers.worktree_fs_server`. `handshake` kills that process in `finally`. Each `git_diff` connects and disconnects. |
| cursor-agent | Worker interpreter, only during `cursor_code` | `cursor_run.py:run_cursor` `Popen`. This spike's child was `spikes/s9_fakes.py --shim --print ...`. A live run is `node.exe` plus the Cursor `index.js` (`cursor_run.py:_cursor_node`), still a child of the worker, still no job object. |

`fleet.py` records the worker pid on `worker.spawned` and drops the process when the wave ends or the pipeline `finally` runs. Nothing writes those pids to disk.

## C. Experiment 1: hard-kill the orchestrator

Three tasks, `fake_sleep=12`. After `worker.spawned` for alpha, beta, and gamma, `taskkill /F` (no `/T`) on the orchestrator.

Killing the venv launcher (`s15aa01`) and killing the cpython interpreter (`s15aa04`) did the same thing. The launcher exits. `taskkill /F` does not kill children. Windows leaves the dead pid as `ppid`. Two seconds later the three workers were still running, and the fake cursor shims were still their children. Twenty seconds later the shims were gone and the three worker processes were still up.

What the workers did without an orchestrator:

- Each worktree at `C:\wt\s15aa01\<task>` and `C:\wt\s15aa04\<task>` had a new commit (`tf alpha`, `tf beta`, `tf gamma`) and `README.md` containing `shim readme`.
- They kept appending `events.jsonl` (`worker.step`, `output_line`, `worker.task_callback`) with `seq` starting at 0.
- No `worker.exited`. No `report.json`. The in-memory results died with the orchestrator.

UI reload is a new process. `GET /api/board` on that repo returned `stage=guard`, `run_id=null`, `tasks=[]`. The git worktrees were still listed (`tf/s15aa01/alpha`, `beta`, `gamma`). `POST /api/runs/s15aa01/cancel` returned 404 `unknown run`. The board does not scan `runs/`.

Second run `s15aa0b` on the same repo, while those worktrees, branches, and live workers still existed: exit 0, alpha/beta/gamma `done`, `isolation_ok` true. Branch names include the run id, so they do not clash. `free_port` skips a port that is already bound. The canary is excluded from `git status`, so the main checkout stayed clean. The second run does not fail. It also does not adopt the first run.

`treefleet cleanup` was not a clean sweep while those workers were alive. `git worktree remove --force` returned `fatal: 'C:\wt\s15aa01\alpha' is not a working tree` (same for beta and gamma, and again for `s15aa04`). The directories and `tf/s15aa01/*` branches were still there until the worker processes were killed and the directories were removed by hand.

## D. Experiment 2: kill one worker

`s15aa02`, three tasks, `fake_sleep=8`. After all three had spawned, `taskkill /F` on the beta worker only (not `/T` on the orchestrator).

The orchestrator noticed in 2.6 seconds. `worker.spawned` for beta was `2026-11-08T03:57:39.032365Z`. `worker.exited` was `2026-11-08T03:57:43.015209Z`. Status `failed`. The error recorded in `events.jsonl` is `RemoteA2aAgent fallback: RuntimeError; A2AClientHTTPError: HTTP Error 503: Network communication error: All connection attempts failed`. `fleet.py:dispatch` tries `RemoteA2aAgent`, then the A2A SDK client, then `_failed`. There is no watcher and no pid poll. The notice is the HTTP call dying.

Alpha and gamma stayed `done` (`worker.exited` about 10 seconds later, when their fake cursor sleep ended). The run finished: `report.json`, `report.md`, `timing.md` are present, exit 0, `isolation_ok` false because beta failed. Review still ran and wrote `review_verdict` and `reaction.fired` for all three tasks.

## E. Experiment 3: Ctrl+C

Production `Popen` does not create a process group, so a console Ctrl+C hits every process on that console. This spike started the orchestrator with `CREATE_NEW_PROCESS_GROUP` and sent `CTRL_BREAK_EVENT` to that group, which is the Windows signal that can be aimed at one group. `fake_sleep=20`, signal sent about a second after the three workers spawned.

The process exited `3221225786` (`0xC000013A`, the console control-C status). The log has no `KeyboardInterrupt` traceback. No worker process with that run id was left. `events.jsonl` stops at the three `worker.spawned` lines. No `worker.exited`, no report. The pipeline `finally` did not get to write anything after the signal.

Worktrees were kept: `C:\wt\s15aa03\alpha`, `beta`, and `gamma`, each still on the repo's init commit, `README.md` still `base`. Branches `tf/s15aa03/alpha`, `beta`, and `gamma` remained. Cleanup is not graceful in the sense of removing worktrees. The workers were stopped. The run was not resumed.

## F. Current commands

| Command or route | Finished run | Running run | Dead orchestrator |
|---|---|---|---|
| `treefleet "<goal>"` (`cli.py:_run`) | Starts a new run. Does not read the old one. | Starts another process. No run lock. | Same. Experiment 1's second run succeeded beside leftovers. |
| `treefleet cleanup <run_id>` | Removes that run's worktrees. Leaves `tf/<run_id>/*` branches. Removes the canary if it names the run. | Does not know the process. `worktree remove` can fail while a worker has the directory. | Same failure mode as experiment 1. Does not kill surviving workers. |
| `treefleet ui --repo` | A process that itself ran the pipeline still has it in memory and can show the report. A new process does not. | Live board for runs started by this process (`POST /api/runs`). | New process: `GET /api/board` is an empty guard stage. Leftover git worktrees still appear. |
| `treefleet reactions explain --run <id>` | Prints `reaction.fired` lines if `runs/<id>/events.jsonl` exists. | Prints whatever has been flushed. | Same. Exit 1 if the file is missing. |
| `treefleet reactions list`, `treefleet models` | No run. | No run. | No run. |
| `treefleet eval` | Writes `eval/report.json`. Not a session control. | Starts live scenarios. | Does not look for a dead run. |
| `treefleet demo preflight` | Checks the demo repo, including stale worktrees. | Same check. Does not stop a run. | Same. |
| `treefleet demo reset --repo` | Removes every non-main worktree, checks out `demo-base`, and deletes `tf/*` branches only after a yes. | Same, with no process kill. `demo-base` is required. | Same. |
| `GET /` | The board page. | The board page. | The board page. It does not list old runs. |
| `GET /api/board` | Snapshot of the in-memory run, including `report.json` if that process wrote it. | Live snapshot from `events.jsonl` plus memory. | Empty guard snapshot. `run_id` is null. |
| `POST /api/runs` | Starts a new run in this process. | Starts another. | Starts a new one. Does not resume. |
| `POST /api/runs/{id}/approve` and `/cancel` | 404 if this process did not start that id. If it did, `Approval.finish` only releases the plan gate. Cancel does not kill workers after approval. | Plan gate only. | 404 `unknown run` (measured). |
| `GET /api/runs/{id}/events` | SSE from `events.jsonl`. The loop ends only when this process has the id in `board.finished`. | Streams new lines. | The file can be read, but a dead run is not in `board.finished`, so the loop does not stop. |
| `GET /api/worktrees`, `GET /api/diff` | Current git worktrees, not a run record. | Same. | Same. Leftovers show up. |
| `GET /api/models` | Model list. No run. | No run. | No run. |

There is no list-sessions command, no kill-task command, and no kill-run command.

## G. AO comparison

`docs/AO_PARITY_GAP.md` describes AO as a long-running daemon and a worker as a durable session. Facts live in SQLite (`activity_state`, `hibernated_at`, `is_terminated`). Display status is derived at read time. A detached chat host can survive a daemon restart. Restore uses the native session id. TreeFleet, in that doc, is one finite pipeline over an in-memory ADK session. Workers are killed at the end of the wave. There is no session row and no resume.

The AO commands named in the sources that doc lists (`docs/cli/README.md`, the session command notes):

| AO command | What it does | TreeFleet |
|---|---|---|
| `ao session ls` | `GET /api/v1/sessions`. Lists sessions with branch, PR, CI, review, activity, and age. `--include-terminated` keeps dead ones. | No list. A directory of `runs/` and `git worktree list` are not a session index. |
| `ao session kill` | `POST /api/v1/sessions/{id}/kill`. Terminates that session. Interactive kill refuses a dirty worktree. | No per-task or per-run kill. Console Ctrl+C stops the group and leaves worktrees. |
| `ao session restore` | `POST /api/v1/sessions/{id}/restore`. Restores a terminated session or resumes an exited agent from the stored workspace and session id. | Nothing to restore. The envelope and ADK state were only in memory. The branch may still exist. |
| `ao session cleanup` | `POST /api/v1/sessions/cleanup`. Reclaims workspaces for terminated sessions. Dirty worktrees are skipped. `--dry-run` previews. | `treefleet cleanup <run_id>` removes one run's worktrees and leaves branches. It does not know terminated versus running, and it failed in this spike while workers were still in the directory. `demo reset` is a demo-base wipe, not a session cleanup. |

## Capability

| Capability | Exists today | Gap | Smallest change |
|---|---|---|---|
| List sessions | No | `runs/` and `git worktree list` are not a status list | One read-only command over `runs/*/report.json` and `events.jsonl` |
| Show one session | Partial | `reactions explain` prints one event type. A new UI process does not open a finished or dead run | Load `runs/<id>/` into the board snapshot that already exists |
| Kill a task | No | A dead worker becomes `failed` only while the orchestrator is alive to see the HTTP error | Record the worker pid and call the existing `kill_tree` for that pid |
| Kill a run | No | Ctrl+C is console-wide. `cleanup` does not kill processes | One command that `kill_tree`s the run's pids, then removes worktrees |
| Resume a run after orchestrator death | No | Results, plan, and verdicts die with the process. Workers can still commit and stay up | Do not resume. Mark the run dead from the partial `events.jsonl` |
| Restore a dead task into a fresh worktree | No | The branch can still exist. The envelope does not | No restore until the envelope is on disk |
| Clean up orphan worktrees and branches | Partial | `cleanup` leaves branches and failed here while workers were alive. `demo reset` is not run-aware | Kill leftover worker pids first, then `worktree remove`, then delete `tf/<run_id>/*` |
