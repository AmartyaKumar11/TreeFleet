# TreeFleet contracts

The Python models in `src/treefleet/contracts.py` are authoritative. This document explains how they travel. If the two disagree, the code wins and this file gets fixed.

## 1. A2A message conventions

Transport: A2A JSON-RPC over HTTP to a worker's base URL. Discovery: `GET /.well-known/agent-card.json`.

| Direction | A2A construct | Payload | Metadata |
|---|---|---|---|
| Orchestrator to worker, start or revise | `message/send` with one `DataPart` | `WorkerTaskEnvelope` | `treefleet/type`=`worker_task`, `treefleet/run_id`, `treefleet/task_id`, `traceparent` |
| Worker to orchestrator, progress | Task status-update message with one `DataPart` (working state) | `ProgressEvent` | same keys |
| Worker to orchestrator, question | Task state `input-required` with one `DataPart` | `InputRequired` | same keys |
| Worker to orchestrator, final | Task artifact named `result` with one `DataPart`, task state `completed` or `failed` | `WorkerResult` | same keys |
| Orchestrator to worker, cancel | A2A `tasks/cancel` | none | none |

Rules:

1. The `DataPart.data` is `model_dump(mode="json")` of the model. The receiver validates with `model_validate`. A validation error is a protocol error, logged as a `RunEvent` of type `protocol_error`, and fails the task.
2. `schema_id` on each model is the version discriminator. A mismatch is a protocol error.
3. A worker accepts exactly one active task. A second envelope while busy is rejected.
4. Revision: the orchestrator sends a new `WorkerTaskEnvelope` with `attempt` incremented and `revision_feedback` set. The worker reuses its worktree and branch and continues from the current commit.
5. Workers must not include file contents in `ProgressEvent.data`. Paths and counts only.
6. Total envelope size under 64 KB.

### Agent card (worker)

```
name: treefleet-worker-<task_id>
description: Implements one scoped task inside a single git worktree by directing a headless Cursor agent.
version: 1
skills: [ { id: implement_task, name: Implement task,
            description: Accepts a WorkerTaskEnvelope, directs cursor-agent to edit files inside its worktree,
                         verifies scope, runs named checks, commits on its branch, returns a WorkerResult.
                         `summary` is built from git facts. The agent's text is `notes`, written only after `git_diff`. } ]
capabilities: { streaming: true }   # set explicitly; verify the server really streams in P0
auth: bearer token from the per-run token
```

## 2. MCP tool contracts

Two stdio MCP servers built with the MCP Python SDK. Every tool returns a Pydantic-validated JSON object. Errors are typed results, not exceptions, so the LLM can recover.

Common result shape: `{ "ok": true, "data": ... }` or `{ "ok": false, "error": { "code": "...", "message": "..." } }`.

Error codes: `PATH_ESCAPE`, `PATH_DENIED`, `NOT_FOUND`, `TOO_LARGE`, `NOT_ALLOWED`, `TIMEOUT`, `BAD_ARGS`, `INTERNAL`.

### 2.1 `worktree-fs-mcp` (one process per worker, started with `--root <worktree>`)

Read-only by design. The CrewAI agent has no tool that writes a file. All edits come from `cursor_code` (section 2.3).

| Tool | Args | Returns | Rules |
|---|---|---|---|
| `list_dir` | `path` (relative, default `.`), `depth` 1 to 3 | entries with type and size | Hides `.git`. Caps at 500 entries |
| `read_file` | `path`, `start_line`, `end_line` | text slice | Rejects binary. Caps at `Limits.max_file_bytes` |
| `search` | `pattern`, `glob` | matches with path and line | Literal match by default. Caps results |
| `run_check` | `name` | `CheckResult` | `name` must be a key in the envelope's `checks`. Runs with `shell=False`, working directory root, timeout, minimal environment, output tails truncated |
| `git_status` | none | short status lines | Read-only |
| `git_diff` | `path` optional | unified diff, truncated | Read-only |

Path resolution for every tool: join with root, `os.path.realpath`, require the result to be inside `realpath(root)`, reject if any path segment resolves through a symlink leaving the root. Windows: also reject drive-letter paths and reserved device names.

Large outputs (more than 6,000 characters) from `run_check`, `read_file` and `search` pass through the relevance filter: split into chunks, ask Jev one `noul` per chunk ("relevant to this task's instructions?") in one batched call, elide chunks below 0.5 with a marker line, and set `elided_by_jev`. If Jev fails, apply head and tail truncation instead.

### 2.3 `cursor_code` (CrewAI tool inside the worker, not an MCP tool)

A plain Python wrapper around `cursor-agent`. It is the only path by which a file changes.

| Field | Rule |
|---|---|
| Input | `instruction` (string, max 6,000 characters). The wrapper prepends a fixed header with the task id, `allowed_paths`, `denied_paths`, "do not run git commands", "do not touch files outside the listed paths" |
| Process | `cursor-agent` 2026.10.22-27dcf5f, `cwd` = worktree, `shell=False`, minimal environment plus the Cursor credential, timeout from `Limits`. Flags: `--print`, `--output-format json`, `--workspace <worktree>`, `--trust`, and `--model <id>` when a Cursor id is resolved. `--trust` is required: without it the headless CLI exits 1 on the Workspace Trust prompt and does not edit. On Windows pass `--sandbox disabled`. `--sandbox enabled` exits 1 and does not start (`Sandbox requires macOS or Linux`, S10b). `--force` does not make a permission file block writes (S10) |
| After the run | Wrapper lists changed and untracked files, reverts any path outside scope, and returns a `CursorRunResult` |
| Output | `CursorRunResult`: `exit_code`, `timed_out`, `duration_s`, `files_changed`, `reverted_paths`, `output_tail` (max 2,000 characters), and `usage` from the CLI JSON when present |

S10, with `--sandbox disabled`, printed one JSON result that included `usage.inputTokens`, `usage.outputTokens`, `usage.cacheReadTokens`, and `usage.cacheWriteTokens`. Other top-level JSON names were not recorded. S10b printed no JSON. There is no usage pool. The Cursor cost line uses this `usage` object and names the CLI JSON as its source.

`CursorRunResult` is a new contract added in Phase 3 under the contract-change rule (test plus a note in `docs/STATUS.md`). `WorkerTaskEnvelope.cursor_model` is already a `ModelChoice | None`. The envelope's `model` field stays the DeepSeek reasoning model.

### 2.4 Model choice

`ModelChoice` matches `^[A-Za-z0-9._:/\-]{1,100}$`.

| Field | Where | Meaning |
|---|---|---|
| `worker_model` | `RunRequest`, default `router` | `router` means TreeFleet routing (UI label Smart routing). Any other value, including Cursor's own `auto`, is one discovered Cursor model id for every task |
| `model_override` | `TaskSpec`, default null | Cursor model id for that task only |
| `cursor_model` | `WorkerTaskEnvelope`, default null | Id passed to `cursor-agent`, or null to omit `--model` |
| `base_sha` | `WorkerTaskEnvelope`, default null | Run base commit. File lists diff this sha through `HEAD`, including a revision's earlier commits |
| `notes` | `RunRequest` and `FinalReport`, default empty | Setup commands that were not run, and checks dropped because they failed on the clean base. Each note is at most 300 characters, at most 20 notes |
| `fleet_wall_seconds` | `RunMetrics`, default 0 | Time from the start of the fleet until the last worker returns |
| `task_seconds` | `RunMetrics`, default 0 | Sum of task durations. `fleet_speedup` is this divided by `fleet_wall_seconds` |
| `sequential_seconds` | `RunMetrics` | Pre-fleet overhead plus task, review and integration time. `speedup` is this divided by `wall_seconds` |
| `review_seconds` | `RunMetrics`, default 0 | Sum of reviewer call durations |
| `integration_seconds` | `RunMetrics`, default 0 | Time spent merging approved branches and running post-merge checks |
| `cursor_model` | `RouteDecision`, default null | Id the router resolved |
| `source` | `RouteDecision` | `override`, `run`, `router`, or `default`. Older values `jev` and `heuristic` still validate |

`router` is TreeFleet routing. The UI label is Smart routing. It is not sent to `--model` and does not have to appear in the discovered list. `auto` is Cursor's own Auto, a normal discovered id. The UI label is Cursor Auto. The CLI passes `auto` through unchanged. Bracketed overrides such as `claude-opus-4-8[context=1m]` are not supported. The rejection names the id and says to run `treefleet models`.

Resolution order for each task:

1. `TaskSpec.model_override`, when set.
2. `RunRequest.worker_model`, when it is not `router`.
3. The router tier table. Jev complexity (`trivial`, `moderate`, `complex`) maps through `models.router` in `treefleet.toml` with `source: jev`. If Jev scores are absent, the planner's `task.complexity` maps the same way with `source: heuristic` (`trivial`/`simple`→simple, `moderate`→moderate, `complex`→complex). Unknown complexity uses `models.router.default_tier` with `source: router`.
4. `models.default`.
5. Omit `--model` and use the CLI default. `cursor_model` is null and `source` is `default`.

Phase 0 measured the flag as `--model`. That run's JSON included a `usage` object with token counts. On 2026-10-27 `cursor-agent --list-models` exited 0. The list includes `auto` (label Auto, marked current default). Router tiers are `composer-2.5-fast`, `composer-2.5`, and `cursor-grok-4.6-high`. `default_tier` is `moderate`.

At run start, every explicit id (an override, a non-`router` `worker_model`, a tier entry, and `models.default`) must appear in the model list discovered from the Cursor CLI. The list is cached for the process. An unknown id fails the run before any worktree is created. The message names the id and says to run `treefleet models`.

Every selection emits `model_selected` (section 5). A worker `CostLine` records that Cursor model on `model`, with `task_id` set. DeepSeek cost stays on the reasoning calls.

### 2.2 `gitops-mcp` (orchestrator side, used by the Reviewer through ADK `McpToolset`)

Read-only tools exposed to the LLM. Mutating operations (worktree add, merge, cleanup) exist only in the Python library, never as MCP tools, so the model cannot call them.

| Tool | Args | Returns |
|---|---|---|
| `worktree_list` | none | porcelain worktree list as structured entries |
| `branch_summary` | `branch` | commit list, files changed with additions and deletions |
| `diff` | `branch`, `path` optional, `max_chars` | unified diff vs the base ref, truncated |
| `show_file` | `ref`, `path` | file text at the ref, size-capped |
| `scope_check` | `task_id` | files changed that fall outside `allowed_paths` |

## 3. External tool: Jev

`POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer <key>`.

Request: `{ "state": "<text>", "model": "jev-latest", "questions": { "<name>": <question> } }`

| Type | Question fields | Answer fields |
|---|---|---|
| `noul` | `instructions`, optional `criteria` for true and false | `noul` as a probability from 0 to 1. No confidence field |
| `choice` | `instructions`, `criteria` as option to description (up to 255). Include an `other` option | `choice`, `confidence`, `probabilities` |
| `score` | `instructions`, `criteria` as an ordered list | `score` as a float on the scale, `legend`, `probabilities`, `confidence` |

Response also carries `model` and `usage` (`input_tokens`, `output_tokens`). Errors: 400, 401, 403, 404, 422 with details, 429 with `Retry-After`, 529 overloaded.

Questions used by TreeFleet:

| Name | Type | State | Meaning |
|---|---|---|---|
| `prompt_injection` | noul | user goal or fetched text | Contains instructions aimed at the AI system |
| `abusive` | noul | user goal | Toxic, hateful or abusive language |
| `on_topic` | noul | user goal | Asks for a software change to a repository |
| `complexity` | score, criteria `trivial`, `moderate`, `complex` | task instructions plus repo map excerpt | Task difficulty for model routing |
| `in_scope` | noul | task instructions plus diff stat | Diff stays within the task's intent |
| `relevant_chunk` | noul | task instructions plus one output chunk | Chunk helps the worker |

Thresholds live in `treefleet.toml`: injection and abusive block at 0.5 or higher, on-topic blocks below 0.3, low-confidence routing falls back to `moderate`.

## 4. ADK session state

| Key | Type | Writer | Readers |
|---|---|---|---|
| `request` | `RunRequest` | API | all |
| `guard_log` | `list[GuardDecision]` | every guard | Reporter |
| `repo_map` | `str` | RepoScan | Planner, Router |
| `plan` | `Plan` | Planner (`output_key`) | Router, FleetAgent, Reviewer, Reporter |
| `routing` | `list[RouteDecision]` | Router | FleetAgent |
| `attempts` | `dict[task_id, int]` | FleetAgent | FleetAgent, Reviewer |
| `worker_results` | `dict[task_id, WorkerResult]` | FleetAgent | Verifier, Reviewer, Integrator, Reporter |
| `isolation` | `IsolationReport` | Verifier | Reporter, UI |
| `verdicts` | `dict[task_id, ReviewVerdict]` | Reviewer | FleetAgent (revise loop), Integrator, Reporter |
| `integration` | `IntegrationReport` | Integrator | Reporter |
| `report` | `FinalReport` | Reporter | API |

Values are stored as `model_dump(mode="json")` and re-validated on read. Writes go through `EventActions(state_delta=...)` so ADK persists and traces them.

## 5. Events (callbacks to logs, UI and Langfuse)

`RunEvent.type` vocabulary, one namespace per source:

- `api.run_started`, `api.run_finished`
- `guard.decision` (payload is a `GuardDecision`). A tool-argument or validation failure is `tool.error`, not a guard decision.
- `tool.error` (payload: `tool`, `message`) when a worker tool fails for a reason other than a missing argument. It does not count as a guard decision and does not move a task to Needs you.
- `tool.arg_error` (payload: `tool`, `message`, and redacted `arguments` when the caller passed them) when a required file-tool argument is missing or blank. The message names the required parameters and their types. It is not a tool failure and does not move the card to Needs you.
- `guard_degraded` (payload: `stage`, `detector`) when Jev is unreachable or times out. The deterministic decision stands. A Jev outage does not block a run.
- `router.jev_skipped` (payload: `reason` of `not_configured`) once per run when `TYPESAFE_API_KEY` is unset or empty. `FinalReport.router` is `"fallback"`.
- `router.heuristic_used` (payload: `tasks` of `{id, tier}`) once per run when any task used planner complexity → router tier (`trivial`/`simple`→`simple`, `moderate`→`moderate`, `complex`→`complex`). Ids and tier names only.
- `guard_redacted` (payload: `count`, `stage`) when a secret pattern was removed. The secret itself is never stored.
- `generated_artifact_reverted` (payload: `paths`) when a tracked file changed outside the task declaration and was restored before commit.
- `orchestrator.agent_start`, `orchestrator.agent_end`, `orchestrator.model_call`, `orchestrator.tool_call`, `orchestrator.tool_result`, `orchestrator.state_delta`
- `git.worktree_created`, `git.worktree_removed`, `git.commit`, `git.merge`, `git.proof`
- `scope_violation` (payload: `path`, `reason`) when a worktree has a `.cursor/` directory or `.cursorrules` file that is not in the base commit. `git.cursor_warning` (payload: `path`, `text`) logs tracked Cursor config. That text is never executed.
- `worker.spawned`, `worker.progress`, `worker.step` (payload is `StepTiming`: `start`, `deepseek`, `cursor`, `check`, or `commit`, with `duration_s` and `timed_out`), `worker.step_callback`, `worker.task_callback`, `worker.guardrail`, `worker.exited`
- `worker.ready` (payload: `pid`, `cwd`, `task_id`) written by the worker process after it changes to the worktree and before it listens. The orchestrator resolves the interpreter from this event. `worker.spawned` payload `pid` is that interpreter, or the string `unresolved`.
- `isolation.proof_detail` (payload: `attempts`) when the process-cwd check fails. Each attempt has `pid`, `pid_started`, `alive`, `child_pids`, and `processes` of `{pid, cwd, error}`. A pass does not write this event.
- `worker.killed` (payload: `reason`, `pid`). Written by `treefleet sessions kill --task`.
- `jev.call` (payload: question names, input tokens, latency, cache hit)
- `model_selected` (payload: `task_id`, `model`, `source` of `override`, `run`, `router`, `default`, or `jev`, and `complexity`)
- `reaction.fired` (payload: `event`, `task`, `action`, `attempt`, `max_attempts`, `escalate`). Written each time `resolve()` runs. See `docs/REACTIONS.md`. `pr_comment` is the reaction for a trusted pull-request comment (send to the agent, two retries).
- `pr.opened`, `pr.checks`, `pr.comment`, `pr.comment_seen`, `pr.comment_ignored`, `pr.revision_started`, `pr.revision_pushed`, `pr.revision_failed`, `pr.checks_green`, `pr.unknown_check_state`, `pr.conflict`, `pr.merged`, `pr.closed`, `check.unavailable`, `worker.start_failed`, `pr.trust_unresolved`, `acceptance.unavailable`. Emitted only when `[pr] enabled` is true, except `check.unavailable`, which the review loop also emits when a local check cannot start. `worker.start_failed` is a worker that exited before its first step; its reason is the last log line, and it does not use a revision round. `pr.trust_unresolved` means the trusted list was empty and the authenticated login could not be resolved; the task goes to Needs you and nobody is trusted. Payloads carry the pull request number, a check label, a timestamped stage, or an author. `pr.comment_ignored` also carries `reason` (`untrusted`, `own`, or `stale`), `comment_id`, and `created_at`. The card notice is only `untrusted` or `stale`. `acceptance.unavailable` (payload: `task`, `reason`) means the reviewer could not be asked; it does not spend a revision round, and the card says `reviewer unavailable`. Comment bodies are not stored. A push does not mark a comment satisfied. See `docs/PR_LIFECYCLE.md`.
- `review_started`, `review_verdict`, `revise_started` (payload: `round`). `[revise] max_rounds` in `treefleet.toml` is the re-dispatch cap (default 1). `round_budget_s` stops a revise and the verdict reason names the budget. A `review_verdict` for an unchanged diff also carries `cursor_message`, Cursor's final text from `WorkerResult.cursor_message`. `ReviewVerdict.suggestions` holds quality notes that do not block approval. `WorkerResult.status` includes `needs_review` for a committed in-scope diff whose review is unresolved. `RunRequest.include_needs_review` merges those branches. A needs_review upstream cancels dependents.
- `phase` (payload: `name` of `scan`, `planner`, `router`, or `fleet`, and `duration_s`)
- `awaiting_approval` (payload: `approved`) when `RunRequest.plan_approval` is true. The CLI prints the plan and asks `Approve plan? [y/N]` unless `--no-approval`. A decline exits without creating worktrees. The HTTP approval endpoint is Phase 8.
- `mcp.call` (payload: server, tool, ok, error code)
- `protocol_error`
- `output_line` (payload is `OutputLine`: one redacted line, max 200 characters, local only)
- `cursor.tool_start`, `cursor.tool_finish` (payload is `CursorToolEvent`: `tool`, `path`, and `ok` on finish). The file body is not stored. `task_id` is the task that launched Cursor.

`seq` is a per-run monotonically increasing integer assigned by the sink. The SSE `id` field equals `seq`, so a reconnecting UI resumes with `Last-Event-ID`.

## 6. HTTP API

| Method and path | Body or result |
|---|---|
| `POST /api/runs` | `RunRequest` returns `{ "run_id": "..." }` |
| `GET /api/runs/{id}/events` | SSE stream of `RunEvent` |
| `GET /api/runs/{id}` | current snapshot: tasks with status, results so far |
| `GET /api/runs/{id}/report` | `FinalReport` JSON, or `?format=md` for Markdown |
| `GET /api/runs/{id}/proof` | live `git worktree list`, per-task proof, main checkout proof |
| `POST /api/runs/{id}/cleanup` | removes worktrees, keeps branches |
| `GET /api/runs/{id}/bundle` | zip of patches and the events log |
| `GET /api/models` | Bearer auth. Discovered Cursor model ids and labels: `{ "models": [ { "id": "...", "label": "..." } ] }`. Does not include the `router` sentinel |
| `GET /healthz` | liveness |

All `/api` routes require a bearer token from the environment.

The CLI accepts `treefleet "goal" --model router|<id>`. The default is `router`. An `<id>` must be a discovered Cursor model id, and `auto` is one of those ids. `treefleet models` prints the discovered ids and labels. The Worker model dropdown lists Smart routing, then Cursor Auto, then the other discovered models. `RunRequest.plan_approval` defaults to true. After the plan is printed the CLI asks `Approve plan? [y/N]` unless `--no-approval`, which sets `plan_approval` false. `treefleet cleanup <run_id>` removes that run's worktrees and leaves the branches.

## 7. Naming and paths

- `run_id`: 8 lowercase alphanumerics.
- Branch: `tf/<run_id>/<task_id>`. Integration branch: `tf/<run_id>/integration`.
- Worktree: `<TREEFLEET_WT_ROOT>/<run_id>/<task_id>`. Default root is `C:\wt` on Windows and `/tmp/tf-wt` elsewhere.
- Run artifacts: `runs/<run_id>/` with `events.jsonl`, `report.json`, `report.md`, `proof.json`, `patches/`.
- Canary: `<main checkout>/.treefleet_canary`, content `canary-<run_id>-<unix_ts>`, excluded through `.git/info/exclude`.
