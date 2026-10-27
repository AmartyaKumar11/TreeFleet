# Status

Spec complete. Phases 1, 2, and 3 are in the tree.

| Phase | Name | State | Evidence |
|---|---|---|---|
| 0 | Spikes | done | `docs/spikes/summary.md` |
| 1 | Skeleton, config, events, gate | done | `evidence/phase-1/gate.txt` |
| 2 | Git layer and isolation proofs | done | `evidence/phase-2/gate.txt` |
| 3 | Worker (CrewAI, MCP, A2A server) | done | `evidence/phase-3/gate.txt` |
| 4 | Orchestrator core (ADK) | not started | |
| 5 | Guardrails, Jev, callbacks, routing | not started | |
| 6 | Review loop and integration | not started | |
| 7 | Observability | not started | |
| 8 | Web UI | not started | |
| 9 | Evaluation, cost, time | not started | |
| 10 | Cloud deployment | not started | |
| 11 | Demo hardening | not started | |

## 2026-10-27 — Phase 3

The worker is an A2A SDK app (`a2a-sdk==0.3.26`). `AgentExecutor` runs the CrewAI crew. uvicorn binds `127.0.0.1`. `capabilities.streaming` is true. Each run requires its bearer token. A second task while one is active is rejected. `A2AServerConfig` is not used.

The CrewAI agent calls `deepseek/deepseek-flash` through LiteLLM and reaches `worktree-fs-mcp` through `MCPServerStdio`. `output_schema`, `response_format`, and `output_pydantic` are not used. The agent returns JSON. Plain code parses `WorkerDraft` and retries once with the validation error. The filesystem server is read-only plus `run_check` by name. The agent has no write tool.

`cursor_code` runs `cursor-agent` with `--print`, `--output-format json`, `--workspace`, and `--model` when `WorkerTaskEnvelope.cursor_model` is set. `None` omits the flag. cwd and `--workspace` are the worktree. `shell=False`. On Windows the `.cmd` launcher is resolved to that version's `node.exe` and `index.js`, because `CreateProcess` cannot run a cmd. The child gets a minimal environment plus `CURSOR_API_KEY` when it is set. The timeout comes from `Limits`. A timeout kills the process tree. There is no sandbox on Windows (`--sandbox disabled`). After every run, `revert_out_of_scope` runs. The wrapper returns a `CursorRunResult`, including CLI usage (`inputTokens`, `outputTokens`, `cacheReadTokens`, `cacheWriteTokens`). Streaming stdout is emitted as `output_line` events: one redacted line, max 200 characters, local only.

A second envelope with `attempt + 1` and `revision_feedback` reuses the worktree and branch and commits on top of the current head. After the crew, plain code reverts out-of-scope paths, runs the named checks, commits on `tf/<run>/<task>`, and builds `WorkerResult` from git. The model never runs git and never writes a file.

Offline tests use `spikes/s9_fakes.py` and a scripted `BaseLLM`. No offline test calls a real API. One live task ran on a throwaway repo (not PR Sentinel) with `composer-2.5-fast`. `WorkerResult` and Cursor usage are in `evidence/phase-3/live.json`. The phase 3 gate passed: 57 tests.

## 2026-10-27 — Model list and semantics

`cursor-agent --list-models` exited 0. The output has an Available models header, `id - label` lines, a `(current, default)` marker on Auto, U+200B at the end of some labels, and a Tip paragraph. Parsing strips those. Nothing is cached to disk. `router` is TreeFleet routing (UI label Smart routing) and stays the CLI default. `auto` is Cursor's own model (UI label Cursor Auto) and is passed through. Bracketed overrides such as `claude-opus-4-8[context=1m]` are rejected. Router tiers are `composer-2.5-fast`, `composer-2.5`, and `cursor-grok-4.6-high`, with `default_tier` `moderate`. A tier id missing from the discovered list fails the run and names the id.

## 2026-10-27 — Phase 2

The git library creates short worktrees (`C:\tf/<run>/<task>` in tests, under 120 characters), writes the canary through `.git/info/exclude`, and removes a worktree only after its process tree is gone, retrying when Windows still holds the directory. `collect_changes` is the branch's commits since the base (`base...HEAD`) plus staged, unstaged, and untracked files. A later commit on the base is not a task change. `revert_out_of_scope` restores tracked files and deletes untracked ones outside `allowed_paths` or inside `denied_paths`.

The cwd check walks the child tree. On Windows the uv launcher's cwd is the temp directory, and the child interpreter's cwd is the worktree. `verify_main` compares status and HEAD and requires the canary. `snapshot_siblings` and `verify_siblings` fail when a worktree that no task owns changes. Cursor has no sandbox on Windows, so the tests write into the main checkout and into a sibling worktree, and both proofs fail. Integration merges approved branches in order. A same-hunk conflict is reported, `git merge --abort` runs, and the repository is left clean. `gitops-mcp` exposes only `worktree_list`, `branch_summary`, `diff`, `show_file`, and `scope_check`. Each returns `{ok, data}` or `{ok: false, error: {code, message}}`. That shape is already in CONTRACTS.md section 2, so it stays next to the server. No contract change.

## 2026-10-27 — Phase 1

`treefleet.toml` and `config.py` load frozen settings and reject unknown keys. Required secrets are `DEEPSEEK_API_KEY` and `TYPESAFE_API_KEY` from the environment only. A missing one fails with the variable name and no value. `CURSOR_API_KEY` is optional. Reasoning models are `deepseek/deepseek-flash` and `deepseek/deepseek-v4-pro`. `default_worker` is `router`. The router table uses `simple`, `moderate`, and `complex`. Contract complexity `trivial` maps to `simple`. `default_tier` is `moderate`, and that tier's model is used when Jev or routing is unavailable. The example Cursor ids `gpt-5` and `sonnet-4-thinking` come from the CLI help text and the mock. S10 did not run a model listing, so they are not a measured catalog.

S10 named `--list-models` in the CLI help and did not execute it. Discovery calls that flag, caches the result for the process, and tests use the S9 fake. `router` is not looked up. An unknown id names the id and says to run `treefleet models`. Cursor flags are `--print`, `--output-format json`, `--trust`, `--model`, and `--workspace`. `sandbox` stays `disabled` on Windows (S10b). Thresholds are injection 0.5, abusive 0.5, and on-topic 0.3. Worker ports start at 9101. The worktree root is `C:\wt` on Windows and `/tmp/tf-wt` elsewhere. Jev is billed at $0.042 per million input tokens. No pricing page was measured, so `jev_source_url` is the API `https://api.typesafe.ai/v1/systemone`.

The event sink assigns a gapless `seq` per run, appends `runs/<run_id>/events.jsonl`, and replays events after `Last-Event-ID`. `emit_sync` is the thread-safe wrapper for CrewAI. The CLI is folder-first. `treefleet "goal"` uses the current directory. `--model auto` stores `worker_model` `router`. `--no-approval` is accepted and ignored until `plan_approval` exists. `run` validates input, builds a `RunRequest`, and prints it. The run itself is Phase 4. No contract change.

## 2026-10-27 — Phase 0 gate

Ruff excludes `.venv*` and `spikes/`. Those are local environments and throwaway measurement scripts, and they do not ship. Pyright runs with `--pythonpath` pointed at `.venv-orch` over `src`, `tests`, and `scripts`, skipping `src/treefleet/worker`. A second run uses `.venv-worker` on that folder only, and only once the folder exists. Spikes and the venv folders are excluded from both.

## 2026-10-27 — Phase 0

Spikes S1 through S5 and S7 through S11 are recorded in `docs/spikes/`. S6 is deferred (no Langfuse keys). S10b: `--sandbox enabled` does not start on Windows, because the CLI requires macOS or Linux. With `--sandbox disabled`, S10 blocked neither a denied file nor an outside write. DeepSeek ids are `deepseek-flash` and `deepseek-v4-pro` through LiteLLM. ADK 2.13.0 and CrewAI 1.15.30. Both extras pin `a2a-sdk==0.3.26`. Workers are A2A SDK apps. Planner, reviewer, and `WorkerDraft` JSON are parsed with Pydantic. `output_schema` and CrewAI `output_pydantic` are not used. The demo runs locally. `treefleet.toml` does not exist yet, so model names were not edited there.

## Contract changes

- 2026-10-27. MCP startup and cursor scope. `Limits.mcp_startup_s` defaults to 15 and is also `[workers].mcp_startup_s` in `treefleet.toml`. The filesystem server is the worker venv interpreter with `PYTHONUNBUFFERED=1` and a minimal environment that keeps `SystemRoot`, `TEMP` and `TMP`. A stuck handshake raises `TimeoutError`. `scope_violation` and `git.cursor_warning` are events. Untracked `.cursor/` or `.cursorrules` fails the task before spawn. Tracked Cursor files are logged and not executed.
- 2026-10-27. Timing and summary. `StepTiming` is the payload of `worker.step`: `start`, `deepseek`, `cursor` (`timed_out`), `check`, and `commit`. `WorkerResult.summary` is built from git facts (path, insertions, deletions). The agent's text moves to `notes`. `WorkerDraft.notes` replaces `WorkerDraft.summary`. The worker accepts notes only after a `git_diff` tool call. `Limits.timeout_s` default is 180. The slowest measured cursor step was 41s and a direct cursor-agent call was 76s; 900 was long enough for a model to launch cursor over and over. `plan_approval` stays scheduled for Phase 8.
- 2026-10-27. Phase 3. `CursorRunResult` and `CursorUsage` record one headless `cursor-agent` invocation, including the CLI token fields. `WorkerDraft` is the JSON the reasoning model returns. `OutputLine` and the `output_line` event carry one redacted local line, max 200 characters. `plan_approval` stays scheduled for Phase 8.
- 2026-10-27. Worker model selection. `ModelChoice` matches `^[A-Za-z0-9._:/\-]{1,100}$`. `RunRequest.worker_model` defaults to `router` (UI and CLI label Auto). `TaskSpec.model_override` and `WorkerTaskEnvelope.cursor_model` are optional Cursor model ids. The envelope `model` field stays the DeepSeek reasoning model. `RouteDecision` gains `cursor_model`. Its `source` also accepts `override`, `run`, `router`, and `default`. Older values `jev` and `heuristic` still validate. `model_selected` is in the event vocabulary. `CursorRunResult`, `plan_approval`, and `output_line` stay scheduled for Phases 3 and 8. S10 measured `--model` and a token `usage` object. It did not list models, record a CLI auto id, or a usage pool. Until Phase 5, a `router` run uses `models.router.default_tier`.

## Architecture change: Cursor CLI as the coding engine
- Workers are CrewAI agents on DeepSeek that direct a headless `cursor-agent` in each worktree. Cursor writes all code. DeepSeek never edits files. Jev classifies.
- New spikes S10 (headless Cursor in a worktree) and S11 (non-interactive auth, container). Phase 0 gate now covers S1 to S11.
- `WorkerTaskEnvelope.cursor_model` is on the contract (2026-10-27). `CursorRunResult` and `output_line` landed in Phase 3. `worktree-fs-mcp` is read-only.

## Design locked
- Dark, AO-style board with Cursor-like surfaces. Details in `docs/design-direction.md`, static mock in `docs/mockup/index.html`.
- Folder-first CLI and UI, auto-detected checks with confirmation, plan approval on by default with an auto-run switch, read-only live output.
- Pending contract change: `RunRequest.plan_approval` (Phase 8).
