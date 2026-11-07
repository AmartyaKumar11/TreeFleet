# Reactions

How TreeFleet responds when something goes wrong or finishes. Step 0 records the behavior that was already in the code. Step 1 reads that behavior from `[reactions.<event>]` in `treefleet.toml`. With no `[reactions]` table, the built-in defaults match this inventory.

A reaction never disables a guard, an isolation proof, scope revert, or the rule that `failed` and `needs_review` tasks stay out of the merge.

## Inventory (before centralizing)

| Event | Where it fires | Current action | Limits | Hard-coded constants |
|---|---|---|---|---|
| Failing checks | `orchestrator/review.py:review_loop` when any `WorkerResult.checks` item has `passed` false, and the error is not the no-changes or upstream short-circuit | One re-dispatch with `revision_feedback` from `check_failure_feedback` | Exactly one round, then the reviewer runs on the new result. A rerun exception becomes a forced reject. No loop if checks are still red. | `_CHECK_FEEDBACK_CAP = 1800` (`review.py`). Not tied to `[revise] max_rounds`. |
| Reviewer revise (`review_revise`) | `orchestrator/review.py:review_loop` when `ReviewVerdict.verdict` is `revise` | Re-dispatch the same branch with `required_changes` or `revision_instructions` (capped at 2000), then review again | `round_no > max_revisions` stops. Default one re-dispatch (`max_rounds` 1), so the loop is rounds 1 and 2. A further revise on a holdable commit becomes `needs_review`; otherwise a forced reject. A round longer than `round_budget_s` stops the same way. An unchanged diff becomes `needs_review` with reason `no new diff`. | `MAX_REVISIONS = 1` (`review.py`). Live value `settings.revise.max_rounds` (`config.py` `Revise.max_rounds`, default 1, range 0..2). `round_budget_s` default 240, max 3600. Feedback slice `[:2000]`. |
| Reviewer reject (`review_rejected`) | `orchestrator/review.py:review_loop` when the verdict is `reject`, including a forced reject and `worker made no changes` / `upstream ` | Stop. No re-dispatch. The branch is excluded by `selected_branches`. `ui/lanes.py:derive_lane` puts `verdict` `reject` in Needs you, so the reaction is `needs_human`. | No retry. `retries` must stay 0. `ignore` is forbidden. | Same review loop. Terminal reject is not a revise round. |
| Merge conflict | `git/merge.py:integrate` calls `resolve_once` once per task. `orchestrator/review.py:IntegratorAgent.resolve_once` merges the integrated tip and re-dispatches with `conflict_revision_feedback`. | Owning task gets one resolve round. | A second conflict on that task excludes it. `resolve_once` is not an LLM merge. | `resolved_once` set in `integrate`. `_CONFLICT_FEEDBACK_CAP = 1800` (`merge.py`). Timeout `round_budget_s + 60`. |
| Provider error | `llm_retry.py:with_llm_retry` and `with_llm_retry_sync`. Callers: reviewer and planner (`orchestrator/agents.py`, `orchestrator/review.py`) and the worker brain (`worker/crew.py`, one model, so no escalate call). | Retry the same model, then one call on the escalate model when a second model was passed. Still down: `ModelUnavailableError`. The review loop turns that into `needs_review` when the commit is holdable (`review.py` around the `ModelUnavailableError` handler), otherwise records the error and a revise verdict that does not re-dispatch. The worker returns `failed` with `model unavailable:`. | Two retries after the first call (three calls on the first model), waits 2s then 6s, then one escalate call with no wait. Non-transient errors are not retried. | `_RETRY_ATTEMPTS = 2`, `_BACKOFF_S = (2.0, 6.0)` (`llm_retry.py`). |
| needs_review / needs_human | Status is set in `review.py:_needs_review` and the reviewer-unavailable branch. `ui/lanes.py:derive_lane` puts that task in Needs you. `ui/lanes.py:run_needs_human` is true for a Needs you lane or plan approval. `selected_branches` drops the branch unless `include_needs_review`. `fleet.py` cancels a dependent whose upstream is `needs_review`. | Notify by the Needs you lane and `needs_human` on the board. No automatic re-dispatch. | The human decides. `include_needs_review` is an explicit run flag, not a reaction. | None beyond the status string `needs_review`. |
| Task failed | `fleet.py:_failed` (worktree, scope, spawn, upstream). `review.py:review_loop` rejects `worker made no changes` and `upstream ` without a revise. Worker `run_task` returns `failed` for scope, timeout, a bad checkout, and a named check. | No retry, except the separate failing-checks path above. Excluded from merge. | No retry count. | None. |
| Approved (green) | `review.py:review_loop` breaks when the verdict is `approve`. `selected_branches` merges only `approve` + `done` + `head_sha`. | No further reaction. The integrator merges the branch. | Suggestions do not block approval (`review.py` auto-approve path). | None. |
| Run done | `orchestrator/pipeline.py:run_pipeline` returns after `ReporterAgent` writes `report.md` and `report.json`. | The process prints the report path and exits 0. | No retry. | None. `api.run_finished` is named in `docs/CONTRACTS.md` and is not emitted today. |

Adjacent loops that are not these events, and stay as they are: reviewer JSON parse has one repair then reject (`review.py:verdict_with_repair`); the planner has one JSON repair (`orchestrator/agents.py`); `worker/draft.py:draft_with_retry` parses once more; CrewAI `guardrail_max_retries=1`.

## Config

Optional tables. Omitted events use the defaults below. `[revise] max_rounds` fills `review_revise.retries` when `[reactions.review_revise]` is absent, so an existing toml keeps today's revise cap.

`checks_failed` and `review_revise` do not share `MAX_REVISIONS`. Each has its own retry count. With the defaults, one failing-check re-dispatch and one reviewer-revise re-dispatch can both run on the same task (two reruns). `[revise] max_rounds` limits only `review_revise`. A `[reactions.checks_failed]` `retries` of 2 still runs two check re-dispatches and does not consume the revise rounds.

| Event | Default action | retries | escalate_after | auto |
|---|---|---|---|---|
| `checks_failed` | `send_to_agent` | 1 | | true |
| `review_revise` | `send_to_agent` | 1 (or `[revise] max_rounds`, max 2) | | true |
| `review_rejected` | `needs_human` | 0 | | true |
| `merge_conflict` | `send_to_agent` | 1 | | true |
| `provider_error` | `send_to_agent` | 3 | 2 | true |
| `task_failed` | `needs_human` | 0 | | true |
| `needs_review` | `notify` | 0 | | true |
| `approved_green` | `ignore` | 0 | | true |
| `run_done` | `notify` | 0 | | true |

`provider_error` attempts 1 and 2 retry the same model (the 2s and 6s waits). Attempt 3 sets `escalate` and calls the second model when one was given. The next failure is `needs_human` (`ModelUnavailableError`, then `needs_review` if the commit is holdable).

`retries` is how many times `send_to_agent` runs. `resolve(event, attempt)` uses a 1-based attempt. Attempt greater than `retries` does not send the agent again. `escalate_after` is optional and must be from 0 through `retries`. The hard cap is 5 even if a caller builds a reaction by hand.

Actions: `send_to_agent`, `escalate_model`, `notify`, `needs_human`, `ignore`.

Forbidden: `needs_review`, `task_failed`, `provider_error`, and `review_rejected` cannot use `ignore`. `review_rejected.retries` must be 0. Keys `guard`, `guards`, `isolation`, `scope_revert`, and `merge_exclusion` are rejected. Unknown events, unknown actions, a retries value outside 0..5, a bad `escalate_after`, or a wrong type is a `ConfigError` that names the key. `load_settings` raises it, so the process does not start.

Every production `resolve` writes one JSONL line:

```json
{"type":"reaction.fired","event":"checks_failed","task":"alpha","action":"send_to_agent","attempt":1,"max_attempts":1,"escalate":false}
```

The sink stores that object in `RunEvent.payload` (and `round` when the review logger adds it). `task` is null for `run_done`.

```toml
[reactions.checks_failed]
action = "send_to_agent"
retries = 1
auto = true

[reactions.provider_error]
action = "send_to_agent"
retries = 3
escalate_after = 2
auto = true
```

`treefleet reactions list` prints the effective table and marks each row `default` or `configured`. `treefleet reactions explain --run <id>` prints `reaction.fired` lines for `runs/<id>/events.jsonl` in order.
