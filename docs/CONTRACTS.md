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
                         verifies scope, runs named checks, commits on its branch, returns a WorkerResult. } ]
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
| Process | `cursor-agent` 2026.10.22-27dcf5f, `cwd` = worktree, `shell=False`, minimal environment plus the Cursor credential, timeout from `Limits`. Flags: `--print`, `--output-format json`, `--workspace <worktree>`, `--trust`, and `--model <id>` when a Cursor id is resolved. On Windows pass `--sandbox disabled`. `--sandbox enabled` exits 1 and does not start (`Sandbox requires macOS or Linux`, S10b). `--force` does not make a permission file block writes (S10) |
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
| `cursor_model` | `RouteDecision`, default null | Id the router resolved |
| `source` | `RouteDecision` | `override`, `run`, `router`, or `default`. Older values `jev` and `heuristic` still validate |

`router` is TreeFleet routing. The UI label is Smart routing. It is not sent to `--model` and does not have to appear in the discovered list. `auto` is Cursor's own Auto, a normal discovered id. The UI label is Cursor Auto. The CLI passes `auto` through unchanged. Bracketed overrides such as `claude-opus-4-8[context=1m]` are not supported. The rejection names the id and says to run `treefleet models`.

Resolution order for each task:

1. `TaskSpec.model_override`, when set.
2. `RunRequest.worker_model`, when it is not `router`.
3. The router tier table. Jev complexity (`trivial`, `moderate`, `complex`) maps through `models.router` in `treefleet.toml`. If Jev or routing is unavailable, use `models.router.default_tier`. Until Phase 5 exists, every `router` run uses `default_tier`.
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
- `guard.decision` (payload is a `GuardDecision`)
- `orchestrator.agent_start`, `orchestrator.agent_end`, `orchestrator.model_call`, `orchestrator.tool_call`, `orchestrator.tool_result`, `orchestrator.state_delta`
- `git.worktree_created`, `git.worktree_removed`, `git.commit`, `git.merge`, `git.proof`
- `worker.spawned`, `worker.progress`, `worker.step`, `worker.task_callback`, `worker.guardrail`, `worker.exited`
- `jev.call` (payload: question names, input tokens, latency, cache hit)
- `model_selected` (payload: `task_id`, `model`, `source` of `override`, `run`, `router`, or `default`, and `complexity`)
- `mcp.call` (payload: server, tool, ok, error code)
- `protocol_error`

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

The CLI accepts `treefleet "goal" --model router|<id>`. The default is `router`. An `<id>` must be a discovered Cursor model id, and `auto` is one of those ids. `treefleet models` prints the discovered ids and labels. The Worker model dropdown lists Smart routing, then Cursor Auto, then the other discovered models.

## 7. Naming and paths

- `run_id`: 8 lowercase alphanumerics.
- Branch: `tf/<run_id>/<task_id>`. Integration branch: `tf/<run_id>/integration`.
- Worktree: `<TREEFLEET_WT_ROOT>/<run_id>/<task_id>`. Default root is `C:\wt` on Windows and `/tmp/tf-wt` elsewhere.
- Run artifacts: `runs/<run_id>/` with `events.jsonl`, `report.json`, `report.md`, `proof.json`, `patches/`.
- Canary: `<main checkout>/.treefleet_canary`, content `canary-<run_id>-<unix_ts>`, excluded through `.git/info/exclude`.
