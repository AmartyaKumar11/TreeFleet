# Sessions

A run is a directory `runs/<run_id>/`. `run.json` is the envelope: the goal, the repo, the base sha, the routing mode, the stage, the orchestrator pid and its start time, and one row per task (branch, worktree, worker pid and start time, status, verdict). The plan is added when it is approved. The file is replaced atomically (temp file, fsync, `os.replace`). It never contains environment variables, API keys, prompts that hold a secret, or tool output.

The orchestrator rewrites `updated_at` every 5 seconds while it is alive.

## States

`treefleet sessions ls` prints one row per envelope.

| State | Meaning |
| --- | --- |
| running | The stage is not terminal, or the orchestrator pid is still the process that started the run, or `updated_at` is under 20 seconds old. |
| done | The run wrote its report. Stage `done`. |
| failed | The run stopped on a guard, a rejected plan, or no report. Stage `failed`. |
| cancelled | `treefleet sessions kill --run` marked the run cancelled. |
| dead | The stage is not terminal, the orchestrator pid is gone (pid and start time both fail to match, so a reused pid is not treated as the orchestrator), and `updated_at` is older than 20 seconds. |

A dead run is not resumed. The plan, the routing and the worker results lived in the ADK session, which dies with the process. `run.json` and `events.jsonl` are enough to inspect the run and to clean up after it. They are not enough to put an agent back in the middle of a task, and this tree does not try.

`treefleet sessions show <run_id>` prints the envelope for a finished run and for a dead one.

`treefleet ui --run <run_id>` opens the replay board on `runs/<run_id>/events.jsonl`. A finished run shows REPLAY. A dead run shows DEAD.

## Commands

```
treefleet sessions ls
treefleet sessions show <run_id>
treefleet sessions kill --task <run_id>/<task_id>
treefleet sessions kill --run <run_id>
treefleet sessions cleanup <run_id> [--yes] [--force] [--include-unmerged] [--repo PATH]
```

`kill --task` kills that worker's process tree, marks the task failed with reason `killed by user`, and appends `worker.killed` to `events.jsonl`. If the orchestrator is still alive it keeps that reason. If the orchestrator is already dead, the command only kills a leftover pid and updates `run.json`.

`kill --run` kills every recorded worker pid, then the orchestrator, and marks the run cancelled.

A pid is killed only when its start time matches the one in `run.json`. A worktree path is used only when it sits under the configured worktree root. `treefleet cleanup` is the same preview as `sessions cleanup` and deletes nothing unless `--yes` is passed.

## Cleanup

`treefleet sessions cleanup <run_id>` prints the pids, worktrees and branches it would touch. Nothing is removed.

`--yes` kills leftover pids first, then `git worktree remove` with no `--force`. `--force` is required when a worktree has uncommitted changes, and those changes are printed first. Only branches named `tf/<run_id>/*` are deleted. A branch that is not contained in `tf/<run_id>/integrated` is left in place unless `--include-unmerged` is passed. The main checkout and every other branch are left alone. A second cleanup that finds nothing prints `nothing to clean`.

## When the orchestrator dies

On Windows every worker is placed in a job object created with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. The orchestrator holds that handle. `taskkill /F` on the orchestrator pid closes it, and Windows kills the workers and the processes they started after they joined the job (the cursor shim, MCP servers). Processes that already existed are assigned explicitly, because a job does not adopt existing children.

On Linux the worker is started in its own session and calls `prctl(PR_SET_PDEATHSIG, SIGKILL)`. That signal is delivered to the direct child. It is not delivered to grandchildren, so a cursor-agent or MCP server started by the worker can outlive it. The call does not exist on macOS. A pid is still never killed unless its start time matches `run.json`.
