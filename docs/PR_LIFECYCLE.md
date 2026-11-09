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
| Pull request merged | | Task `done` |
| Pull request closed unmerged | | Task `cancelled`, reason recorded |
| Round budget used up | | Task `needs_review` with the last failure summary |

Events: `pr.opened`, `pr.checks`, `pr.comment`, `pr.comment_ignored`, `pr.revision_started`, `pr.revision_pushed`, `pr.conflict`, `pr.merged`, `pr.closed`.

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
