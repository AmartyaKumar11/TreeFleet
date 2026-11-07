# Status

Spec complete. Phases 1 through 6 are in the tree.

| Phase | Name | State | Evidence |
|---|---|---|---|
| 0 | Spikes | done | `docs/spikes/summary.md` |
| 1 | Skeleton, config, events, gate | done | `evidence/phase-1/gate.txt` |
| 2 | Git layer and isolation proofs | done | `evidence/phase-2/gate.txt` |
| 3 | Worker (CrewAI, MCP, A2A server) | done | `evidence/phase-3/gate.txt` |
| 4 | Orchestrator core (ADK) | done | `evidence/phase-4/gate.txt` |
| 5 | Guardrails, Jev, callbacks, routing | done | `evidence/phase-5/gate.txt` |
| 6 | Review loop and integration | done | `evidence/phase-6/gate.txt` |
| 7 | Observability | not started | |
| 8 | Web UI | lite board | `evidence/phase-8/` |
| 9 | Evaluation, cost, time | done | `eval/report.md` |
| 10 | Cloud deployment | not started | |
| 11 | Demo hardening | not started | |

## 2026-10-28 — Revise budget

`treefleet.toml` `[revise]` sets `max_rounds` to 1 and `round_budget_s` to 240. A revise that exceeds the budget stops and the report records `revise round exceeded <seconds>s`.

## 2026-10-28 — needs_review

`WorkerResult.status` may be `needs_review` when checks passed, the diff is in scope, and review requests are still open after the rounds or a revise with no new diff. It is excluded from `tf/<run>/integrated` unless `RunRequest.include_needs_review` (`treefleet --include-needs-review`). A `needs_review` upstream cancels dependents. `failed` remains the status for failing checks, scope violations, and an empty diff.

## 2026-10-28 — Reviewer suggestions

`ReviewVerdict.suggestions` holds quality notes. A revise or reject stands only for failing checks, files outside scope, an empty diff, tests that assert nothing, or a function named in the task with no coverage. Anything else is approved and the model's notes move to suggestions.

## 2026-10-28 — Run aa85638e cursor message

`WorkerResult.cursor_message` stores Cursor's final message. A revise whose diff matches the previous round puts that message on the `review_verdict` event, the board card, and the report line `<task> cursor:`.

## 2026-10-28 — Cursor stream events

`cursor-agent` supports `stream-json` and `--stream-partial-output`. The sample is `docs/spikes/s12-cursor-stream.sample.txt`. Assistant deltas, tool start, and tool finish are events. The result line supplies `CursorUsage`. Contract addition: `CursorToolEvent`.

## 2026-10-28 — Demo kit

`treefleet demo preflight` prints green or red for the two API keys, the Cursor login, the model list, `demo-base`, a clean tree, and leftover worktrees. It does not print a secret value. `treefleet demo reset` removes extra worktrees, returns the repo to a clean `demo-base`, and deletes `tf/*` branches only after a yes. `treefleet ui --replay` plays `events.jsonl` at the recorded gaps, capped at 30 seconds, with a REPLAY bar and the Run button off. The dry run is `1d4d116d` in `evidence/phase-8/`: 343 seconds, isolation PASS, notifications approved, admin rejected after a revise with no new diff.

## 2026-10-28 — Phase 8 lite board

`treefleet ui --repo <path>` serves the dark board on `127.0.0.1`. The page starts the same pipeline as the CLI. Plan approval is on unless Auto-run is switched on, and the plan card waits on Approve or Cancel. Events arrive as Server-Sent Events from `events.jsonl`, redacted, and the live pane is read-only. The worktrees panel polls `git worktree list` and a per-worktree status count once a second. A non-local Host is rejected, `/api` requires the page token, and a goal or path that fails the input guard is not started. The diff route only reads a worktree of that repo. No new contract.

## 2026-10-28 — Eval fixes

E5 now forces the plan through the Phase 5 scripted-plan hook (`TREEFLEET_FORCE_PLAN`, a plan whose only path is `../x`). The rerun was rejected by the plan guard. The planner default is `deepseek/deepseek-flash`, with one escalation to `deepseek/deepseek-v4-pro` after a plan-guard rejection. E1 reran on flash and passed. E2#2's extra time is the 186s revise round on `add-admin-tests`. Earlier scenarios still show the v4-pro planner they actually used.

## 2026-10-28 — Phase 9 evaluation

`treefleet eval` runs `eval/scenarios.toml` once per scenario, and E2 twice, against `D:\pr-sentinel-demo` reset to `demo-base`. There are no retries. The report is `eval/report.md` and `eval/report.json`, copied to `evidence/phase-9/`. E1, E2 (both runs), E3, E4 and E6 passed their expectations. E5 failed and was left as a failure: the planner did not emit `../x`, so the plan was not rejected. Worker tokens are `usage not reported` because CrewAI `token_usage` and the LiteLLM callback were empty. The Estimates section is an extrapolation from measured sequential time per task, batched by `workers.max_workers` (3). Worktrees were removed after each scenario and the main checkout was clean at the end.

## 2026-10-28 — Phase 6 live fixes

Sequential time is pre-fleet overhead plus task, review and integration time. The report lists review and integration on their own lines. `timing.md` records scan, baseline, planner, fleet, review, revise and integrate. Cursor cost is one row per call, including revise rounds, labeled with the task and round. Reviewer calls are their own rows on `deepseek/deepseek-flash`, with `deepseek/deepseek-v4-pro` only after flash JSON stays unparseable. A revise round whose diff matches the previous round stops with reason `no new diff`. Worker tokens come from CrewAI `token_usage` when the LiteLLM callback is empty; if both are empty the worker row says `usage not reported` and the notes say why. A reverted out-of-scope edit that the next proof finds clean is `NO (reverted)` and the overall line is `PASS (1 contained violation)`. Overall stays `FAIL` when the main checkout changed, the canary is missing, a worktree proof fails, or a violation is still present. An unknown Cursor id falls back to the default tier and the `model_selected` event notes the fallback. Contract additions: `CursorCall`, `ReviewerUsage`, `WorkerResult.cursor_calls`, `WorkerResult.reverted_scope`, `RunMetrics.review_seconds`, `RunMetrics.integration_seconds`, and route source `fallback`.

## 2026-10-27 — Phase 6

The reviewer is an ADK `LlmAgent` on `models.strong` (`deepseek/deepseek-v4-pro`), one per finished task, gathered with `asyncio.gather`. `output_schema` stays unset. The verdict JSON is a `ReviewVerdict` (`approve`, `revise`, or `reject`) with `reasons` and `required_changes`, parsed with one repair retry. Its tool list is the gitops read-only set. A revise re-runs the same worktree and branch with those changes appended to the cursor prompt, at most twice, and the isolation proofs run again. A third revise becomes `reject` and that task is left out of the merge.

Integration merges approved branches, in dependency order, into `tf/<run>/integrated` inside its own worktree. The user's branch and the main checkout are not checked out. A conflict aborts that merge, merges the integrated tip into the owning task worktree, and gives that worker one Cursor revise to resolve markers; a second conflict marks the task `conflict` and excludes it. Failed local checks also revise before the reviewer. Board lanes and `needs_human` are derived in Python for SSE. Baseline checks then run on the integrated worktree. `FinalReport` adds Verdict and Revise rounds columns and an Integration section with the merge command.

Cost rows are planner, worker (DeepSeek brain), cursor (the Cursor model and its token counts), and jev. A cursor call with no usage prints `usage not reported` and does not reuse the brain counts. A run with one task prints `n/a (single task)` for both speedup lines. Rates in `treefleet.toml` are from the DeepSeek pricing page and the Cursor models page, fetched 2026-10-27. Cursor rows are labeled `estimate`. `cursor-grok-4.6-high` is unset: that page lists Grok 4.6 and not that id. No price was invented.

`--saboteur TASK` is demo-only and off unless passed. It appends a break-an-existing-test line to that task's cursor prompt, and plain code also adds `test_treefleet_saboteur_demo` so the suite fails when Cursor does not. Contract additions: `RunRequest.saboteur_task`, `WorkerTaskEnvelope.saboteur`, `WorkerResult.brain_usage`, `brain_usage_reported`, `cursor_model`, `cursor_usage_reported`, `ReviewVerdict.reasons`, `required_changes`, `IntegrationReport.checks`, `excluded`, `merge_command`, `user_branch`, `RunMetrics.task_count`, and the DeepSeek and Composer fields on `Pricing`.

Live on `D:\pr-sentinel-demo`. Clean run `d89593f5`: all three tasks approved, branch `tf/d89593f5/integrated`, post-merge checks passed, and no `.pyc` in any task commit. Saboteur run `c412b654` (`--saboteur notifications-tests`, which also matches `add-notifications-tests`): that task's pytest failed, the reviewer rejected it after two revise rounds, the other two stayed approved, the integrator excluded it, and the post-merge checks passed. Its isolation row is out of scope because the demo break edits `tests/test_auth.py`. The worker brain row is `usage not reported`; the callback did not return token counts, and none were invented.

## 2026-10-27 — Phase 5

The input guard runs before the planner. A deterministic check is authoritative: length, control characters, hidden characters, injection phrases, `curl|sh`, `.git`, `.cursor`, and secret-shaped strings. Jev noul then scores prompt injection, abuse, and on-topic. A Jev timeout or transport error emits `guard_degraded` and keeps the deterministic decision. It never blocks the run by itself. A blocked goal exits 1, writes `report.md`, and creates no worktree.

The plan guard rejects absolute paths, `..`, `.git`, `.cursor`, `.env`, `*.pem`, and `id_rsa*`, plus an empty edit list, an unknown dependency, and a task count above `guards.max_tasks` (default 6). The planner gets one repair. If `models.strong` contains `flash` and the repair still fails, one more attempt uses `deepseek/deepseek-v4-pro`. The default planner stays `deepseek/deepseek-v4-pro`.

Tool arguments reject traversal and unnamed checks. Tool results and the report files are redacted. `guard_redacted` stores a count, not the secret. The CrewAI task guardrail retries a draft with an empty summary, a secret, or a file that is not in the git diff. ADK agent, model, and tool callbacks and the CrewAI step and task callbacks emit events with `run_id` and duration. Prompts are not copied into the log.

`PYTHONDONTWRITEBYTECODE=1` is set for checks and workers. Before each commit, a tracked change that the task did not declare and Cursor did not edit is restored. The report notes the paths. A repo that tracks `.pyc` or `node_modules` gets a preflight warning.

Cursor usage and DeepSeek usage fill `CostLine`. The Jev rate is `pricing.jev_usd_per_million_input`. DeepSeek and Cursor rates are unset, so those rows print the token counts and `rate unknown`. A configured Cursor rate would be labeled `estimate`. `TokenUsage.cache_write_tokens`, `CostLine.label`, `CostLine.component` `cursor`, and `WorkerResult.reverted_generated` are the contract additions.

The phase 5 gate passed: 105 tests. Evidence is `evidence/phase-5/guard-demo/`. Run `9824087d` completed a README edit, isolation PASS, Jev routed it to `composer-2.5-fast`. Run `71db6a5f` blocked an injection goal (injection 0.99) with no new worktree. Run `1d1db950` rejected a forced `../x` plan.

## 2026-10-27 — Phase 4

The orchestrator is an ADK pipeline: RepoScan, Planner, Router, FleetAgent, Verifier, Reporter. The planner is an `LlmAgent` on `models.strong` (`deepseek/deepseek-v4-pro`). `output_schema` is unset. Plan JSON is parsed with Pydantic and one repair retry, then stored at state `plan`. Until Phase 5, `router` selects `models.router.default_tier` and emits `model_selected`. FleetAgent creates worktrees, starts each worker with the worker venv interpreter, a minimal environment plus the per-run token, and a free port, then waits for the agent card. The tracked pid is the interpreter child. `RemoteA2aAgent` sends the envelope. A failure there falls back to the A2A SDK client and the exit event records that. Processes are killed in a `finally` block. The verifier calls the Phase 2 proofs. The reporter writes `FinalReport`, `report.json`, and `report.md`. `cursor_model` is in the report. A dirty checkout is refused before any worktree. A declined plan exits 0 with no worktrees. `treefleet cleanup <run_id>` removes worktrees and keeps branches. A bare `cursor-agent` on Windows is resolved to that version's `node.exe` and `index.js`.

Offline tests pass fakes only through `OfflineFakes`: a scripted ADK `BaseLlm`, `spikes/s9_fakes.py`, and `TREEFLEET_MOCK_LLM`. They spawn real worker processes.

One live run on `D:\pr-sentinel-demo`, goal about notifications tests, admin tests, and TypeScript client types, `--no-approval`, router tier `composer-2.5`. Run `71e5f9b6`, wall 344.4s. The plan had six tasks. `document-client-exported-types` finished. A read-only task made no commit, and every dependent was skipped before `git worktree add`, so `IsolationReport.ok` is false. The diagnosis is `evidence/phase-4/diagnosis.md`. Measured on that machine: porcelain paths and proof paths differ as strings and still match after `normcase` and `resolve`; two real `cursor-agent` processes overlapped and both exited 0; three fake workers finished inside the 15s MCP, 40s card, and 180s task limits; four simultaneous `git worktree add` calls all registered. No cursor concurrency limit was added.

A finished upstream with no new commit no longer blocks dependents, unless the error is a main-checkout mutation. The upstream HEAD is the base commit. Notes longer than 3000 characters are clipped. Plan complexity `low`, `medium`, and `high` map to `trivial`, `moderate`, and `complex`. The second live run is `4ef6b7cc` in `evidence/phase-4/live-2/`, wall 319.0s, isolation PASS. The inspect task failed because it never called `git_diff`. `test-notifications`, `test-admin`, and `document-client-exported-types` committed. The main checkout was unchanged and the canary was still present.

A task whose title or instructions start with inspect, review, explore, investigate, run, or check is rejected. The planner is asked once more with that reason, then the run fails with `plan rejected:`. Checks come from `.github/workflows/ci.yml` run steps, the `test`, `lint`, and `typecheck` scripts in `package.json`, and pytest or ruff config. The CLI prints them and asks `Use these checks? [Y/n]`. `--yes` accepts them, `--no-checks` drops them, and repeatable `--check name=cmd` sets a command. Confirmed commands are `RunRequest.checks`. Empty `check_ids` are filled from the edited paths. `run_check` keeps `PATH` and `COMSPEC` and drops secrets. On Windows, `npm` and `npx` run as `node` plus the matching cli script. `WorkerTaskEnvelope.base_sha` is the run base commit. File lists diff that sha through `HEAD`, so a revision's earlier commit stays in the list. `runs/` is gitignored.

Live run `dbd07456` is in `evidence/phase-4/live-3/`, with `--yes --no-approval`. Three edit tasks shared one wave. There was no inspect task. Checks ran and are on each row. Isolation PASS. Wall 134.4s, sequential 196.8s, speedup 1.46x. The three `composer-2.5` calls overlapped (43.7s, 45.8s, 51.1s) inside a fleet of about 70s. Scan, plan, and router account for the rest of the wall clock and are not in the sequential sum. Each task failed a check before commit, so the file lists are empty. `python -m pip install pytest` refused the uv-managed interpreter. `python -m pytest -q` then failed with no pytest module. `npm test` exited 1. The same node test command passed 3 tests. The demo's pytest suite was run separately with the orchestrator interpreter in a worktree: 28 passed, recorded in `evidence/phase-4/checks.txt`.

Setup commands (`pip install`, `npm ci`, `npm install`, `uv sync`) are notes and are never executed. Each remaining check runs once on the clean base in a temporary worktree. Only exit 0 is kept. The interpreter is `--python`, then a repo `.venv` or `venv`, then the first `python` on `PATH` that can import pytest, then the orchestrator interpreter. The bare `python` token is replaced with that path, and its directory is prepended to the check `PATH`. `RunMetrics.sequential_seconds` is pre-fleet overhead plus the sum of task durations. `fleet_speedup` is that sum divided by `fleet_wall_seconds`. The phase 4 gate passed: 90 tests.

Live run `55d0986a` is in `evidence/phase-4/live-4/`. Three tasks finished in one wave, checks passed, isolation PASS. Wall 126.2s, sequential 236.0s, speedup 1.87x, fleet wall 79.3s, fleet speedup 2.4x. Scan 0.031s, baseline checks 2.671s, planner 44.484s, router 0.016s. One `deepseek-flash` planner call for the same goal took 17.860s and returned three edit tasks with empty dependencies. The default planner stays `deepseek/deepseek-v4-pro`. Both plans are in `evidence/phase-4/planner-compare.md`. The worker environment keeps `COMSPEC`, which `npm test` needs in order to launch its script.

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

- 2026-10-27. Plan approval. `RunRequest.plan_approval` defaults to true. `awaiting_approval` records the decision. `--no-approval` skips the prompt. `FinalReport.routing` lists each task's `cursor_model`. The HTTP approval endpoint stays in Phase 8.
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
- Pending: the HTTP approval endpoint (Phase 8). `RunRequest.plan_approval` landed in Phase 4.
