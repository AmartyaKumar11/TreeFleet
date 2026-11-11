# Demo

> Superseded by docs/DEMO_RUNBOOK.md; contains machine-specific paths

Two agents edit `<demo-repo>` in separate worktrees. The board is on this machine only. Say that the yellow REPLAY bar means a recording. A live run has no such bar.

## Before anyone is watching

From `<repo>`:

```
treefleet demo preflight --repo <demo-repo>
treefleet demo reset --repo <demo-repo>
```

Preflight prints green or red per check and a fix command. It does not print key values. Reset removes extra worktrees and puts the repo back on `demo-base`. It lists `tf/*` branches and deletes them only after a yes.

Open the board, then the worktree window:

```
treefleet ui --repo <demo-repo>
powershell -File scripts\demo-watch.ps1
```

The board listens on `127.0.0.1:8765`.

## The goal

Paste this. It asks for two tasks:

```
Add unit tests for src/notifications.py and, as a second task, add unit tests for src/admin.py.
```

Leave the worker model on Smart routing. Leave plan approval on (the Auto-run switch stays off).

## What to say, and what to click

The stage pill is the clock. Measured runs are a range, not a promise for this goal. One-task runs in `eval/report.md` finished in 54–75 seconds. Three-task runs finished in 250–515 seconds, and one of those spent 186 seconds in a revise round. This two-task goal has not been timed as a scenario. It should fall between those, and it runs longer if a review asks for a revision. One dry run of this goal took 343 seconds: the notifications task was approved, the admin task was rejected after a revise with no new diff, and isolation was PASS. That is one sample.

| Stage | Click | Say |
|---|---|---|
| guard | Run | The input guard scores the goal before any plan. A blocked goal never creates a worktree. |
| plan | Nothing | The planner is writing tasks. The model name on the cost table is the one this run used. |
| approve | Approve plan | This card is the plan. Cancel throws it away. Nothing is edited until you approve. |
| fleet | Nothing | Each task has its own directory under `C:\wt` and its own branch. The worktree window is the same list git sees. The branch map draws as those branches appear. |
| review | Nothing | A finished task moves to In review. A revise comes back to Needs you with a round badge. |
| integrate | Nothing | Approved branches merge into `tf/<run>/integrated`. The checkout you started from stays on `demo-base`. |
| done | A task card, then Proof | The isolation table is from git and the process, not from the worker's own report. Within scope is yes only when the proof says so. |

Live output is read-only. The Feedback box is not sent to the worker.

## If a stage stalls

Leave the board up. In the other window, `scripts\demo-watch.ps1` is already listing `C:\wt`. Open `runs\<run-id>\events.jsonl` and, once it exists, `runs\<run-id>\report.md`.

If you need the picture to keep moving, stop treating this run as live and play a recording. The bar says REPLAY on every screen, the Run button is off, and the output tab says Recorded output.

```
treefleet ui --repo <demo-repo> --replay evidence\phase-8\events.jsonl --port 8766
```

If that file is not there yet, use the phase 6 clean run:

```
treefleet ui --repo <demo-repo> --replay evidence\phase-6\live-clean\events.jsonl --port 8766
```

Playback follows the recording's timestamps. A gap longer than 30 seconds is played as 30 seconds, so a recording is not a claim about the original speed. Say that it is a recording.
