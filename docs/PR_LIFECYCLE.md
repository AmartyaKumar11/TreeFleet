# Pull request lifecycle

`[pr]` is off unless the table is present and `enabled` is true. Off means the integrator still builds `tf/<run>/integrated` and no `pr.*` event is emitted.

## Config

```toml
[pr]
enabled = false
repo = "owner/name"
base = "demo-base"
poll_seconds = 15
max_wait_minutes = 60
trusted_commenters = []
auto_merge = false
allow_external_hooks = false
```

`poll_seconds` is at least 5. `repo` is required only when `enabled` is true, and it must be `owner/name`. An empty `trusted_commenters` list means the authenticated `gh` login. Unknown keys are a startup error, same as `[reactions]`.

`treefleet.toml` in this repo does not contain `[pr]`. That is the off path.

## What the stage does

After review approves a task, TreeFleet pushes `tf/<run_id>/<task_id>` and opens a pull request. The title is the task title. The body is the goal, the task scope, a short review note, a checks summary, and the run id. Nothing else is posted at open time.

`run.json` stores `pr_number`, `pr_url`, `pr_head_sha`, `pr_last_comment_id`, `pr_open`, `pr_status`, and `pr_checks` on the task. The run stage becomes `awaiting_prs` while any of those pull requests is open. The heartbeat keeps running. The run ends when every pull request is merged or closed, or when `max_wait_minutes` passes. A timeout sets the stage to `timed_out` and leaves the pull requests open.

The integrated branch is not created in this mode. The stage strip shows `pr` instead of `integrate`.

## Events and states

| Observation | Reaction | Result |
|---|---|---|
| Check failed on the current head | `checks_failed` | Re-dispatch in the same worktree, commit, push, comment `Revision N: ...` |
| Trusted comment | `pr_comment` (send to the agent, 2 retries) | Same re-dispatch, comment text passed as delimited data |
| `mergeable` is `CONFLICTING` | `merge_conflict` | One `git merge --no-edit` of the base into the task branch, then resolve, commit, push |
| Checks green, no unresolved trusted comment | `approved_green` | Card moves to Ready to merge. The global default stays `ignore`; this mode notifies when that default was not overridden |
| Pull request merged | | Task `done`, hidden from Ready to merge. Chip reads `PR #n merged` |
| Pull request closed unmerged | | Task `cancelled`, Needs you, reason recorded |
| Check cannot start | `check.unavailable` | Needs you with the reason. Not a failed round. Does not use a retry |
| Round budget used up | | Task `needs_review` with the last failure summary |

The lane is the latest `pr_status` for that task. `merged` is Done. `cancelled` is Needs you. An earlier `approve` does not keep a merged card in Ready.

Events: `pr.opened`, `pr.checks`, `pr.comment`, `pr.comment_seen`, `pr.comment_ignored`, `pr.revision_started`, `pr.revision_pushed`, `pr.revision_failed`, `pr.checks_green`, `pr.unknown_check_state`, `check.unavailable`, `pr.conflict`, `pr.merged`, `pr.closed`.

## Check interpreter

The first worker run and a revision started by `treefleet pr watch` both call `select_checks`. That function keeps the first interpreter that can import pytest: the repo `.venv`, then `PATH`, then the orchestrator interpreter. It rewrites a bare `python` in the check command to that absolute path. Watch does not replace the path. The worker process is still the CrewAI interpreter. It is not the interpreter that runs pytest.

Phase 11.14's first `bind_python` treated any token whose file name was `python.exe` as bare, including the absolute path `select_checks` had already chosen. It replaced that path with `.venv-worker`. That interpreter cannot import pytest, so the check exited at once with `No module named pytest`. Those two rounds were recorded as `pr.revision_failed` / "revision made no commit".

## Round arithmetic

`pr_comment` retries is 2. That is two counted revision dispatches for one trusted comment. Attempt 3 does not send the agent. `[revise] max_rounds` is the reviewer's re-dispatch cap (1 in this repo, at most 2). It does not raise the comment cap. The task envelope allows `attempt` 1 through 5. There is no separate per-task cap of 3.

A revision that makes no commit counts as one of the two. Two such rounds set the comment to `needs_human` and the card to Needs you. A third dispatch does not run.

A check that cannot start does not count. `rounds_used` stays where it was, and the card goes to Needs you with the `check.unavailable` reason. A later attempt still has both retries.

On run `c6dbaa59`, pull request 84's recorded comment is id `6743492754`, status `satisfied`, `rounds_used` 1. The event log also has two earlier `pr.revision_failed` rows for the same comment. Those two were the pytest-not-found rounds. The loop stopped at 2. The committing round ran only after the local comment cursor and `rounds_used` were cleared. That reset is what allowed a third dispatch. With this rule those two would not have counted, and the commit would have been round 1 of 2.

`treefleet pr status <run_id>` prints one row per task. `treefleet pr watch <run_id>` attaches the poller again from `run.json` after the orchestrator has stopped. `treefleet pr merge <run>/<task>` is the only command that merges, besides `auto_merge`.

## Safety

- The only push refspec is `tf/<run_id>/<task_id>:tf/<run_id>/<task_id>`. Never `main`, never the base branch, never `--force`.
- Before every push, `git remote get-url origin` must be the configured `owner/name`. A mismatch aborts with `origin does not match pr.repo` and does not print the remote URL.
- The poller calls `get_pr` and `list_comments` only for numbers this run opened.
- Comments are untrusted. Only `trusted_commenters` count. TreeFleet's own comments contain `<!-- treefleet -->` and are ignored. Text is capped at 1800 characters, run through the input guard, and wrapped in `<untrusted-data>` in the worker prompt. An untrusted comment emits `pr.comment_ignored` with the author and no body.
- Bodies and comments do not include tokens, host absolute paths, environment values, or raw diffs.
- `merge()` is not called unless `auto_merge` is true or the user runs `treefleet pr merge`.
- Worktrees for tasks with `pr_open` are left in place by session cleanup.
- If a webhook host is not GitHub and `allow_external_hooks` is false, opening pull requests is refused. The demo repo has one hook on `backend-production-af37c.up.railway.app` for `pull_request` and `push`. That host is external, so the switch stays false until someone sets it.

Preflight, only when `[pr]` is enabled: `gh auth status` succeeds, the login is known, the login can push to `repo`, origin matches `repo`, and `base` exists on the remote. A missing workflow on the base is a warning.

## Provider

`PullRequestProvider` is `open_pr`, `get_pr`, `list_comments`, `post_comment`, `get_failed_check_summary`, and `merge`. `GhProvider` calls `gh` with an argument list, `--repo` from config, JSON output, and three attempts with backoff on rate limits and 502/503. `FakeProvider` is in-memory and is what the tests use.

A later PR Sentinel service can implement the same protocol: accept the head, base, title, and body, and return the number, url, and head sha. The poller would keep calling `get_pr` and `list_comments` for the numbers TreeFleet opened. It would not list the repository's other pull requests. `merge` stays unused unless `auto_merge` is set.
