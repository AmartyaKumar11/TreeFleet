# TreeFleet build phases (loop engineering)

Twelve phases, each a closed loop: spec, build the smallest slice, run the gate, fix until green, record evidence, move on. Hour figures are rough estimates for one person working in Cursor, about 56 hours in total.

## The loop protocol

**Inner loop (minutes), repeated until green**

1. Open Cursor in Plan mode and paste the phase prompt. Approve the plan only if it touches the files listed for the phase.
2. Write the tests first, then the smallest implementation that passes them.
3. Run `python scripts/gate.py --phase N`. It runs ruff, pyright and `pytest -m "phase0 or ... or phaseN"`, so earlier phases are re-tested every time.
4. If red, paste the failing output verbatim into Cursor with this instruction: "Fix the root cause. Do not weaken, skip or delete tests."
5. After three red iterations on the same failure, stop. Write `docs/spikes/N-<topic>.md` covering what failed, what you learned and the options. Pick the fallback named in the phase, or change the architecture on purpose.

**Outer loop (once per phase)**

1. Save proof under `evidence/phase-N/` (command output, screenshots, run JSON). The requirement trace points at these files.
2. Re-read the `ARCHITECTURE.md` and `CONTRACTS.md` sections the phase touched. If reality differs, fix the document in the same commit. Doc drift counts as a bug.
3. Update `docs/STATUS.md` (what works, what is fake, what is next), commit `phase N green`, and tag `phase-N`.

**Rules that never bend**

- Never start phase N+1 on a red gate.
- Every phase leaves the main branch runnable.
- Tests marked `live` call real model APIs. They are excluded from the default gate and run once at the end of a phase with `--live`.
- Offline tests use scripted fake LLMs, so the loop is fast and free. Phase 0 decides the fake-LLM mechanism for each framework.
- Contracts change only in `contracts.py`, with a test, and the change is mentioned in `STATUS.md`.

---

## Phase 0: Spikes (3 h)

**State:** done, 2026-10-27. Notes in `docs/spikes/`. S6 stays deferred.

**Goal:** turn every `[verify P0]` item into a measured fact before building on it.

**Spikes** (each is a script under `spikes/` plus a note in `docs/spikes/` ending with `Result: PASS`, `Result: FAIL` or `Result: FALLBACK <name>`):

| Id | Question | Fallback if it fails |
|---|---|---|
| S1 | FALLBACK. ADK 2.13.0. Ids `deepseek-flash` and `deepseek-v4-pro`. `output_schema` gets 400, `response_format` unavailable. A tool call works without it | Drop `output_schema`, parse JSON with Pydantic and retry with the validation error |
| S2 | Does a CrewAI agent on DeepSeek call an MCP tool? Which MCP mechanism does the installed version offer | Use the version's tool adapter for MCP |
| S3 | FALLBACK. crewai 1.15.30. `A2AServerConfig` only builds an agent card. Nothing serves HTTP, so streaming and input-required were not observable | Serve through an A2A SDK app whose executor runs the crew |
| S4 | PASS. `RemoteA2aAgent` on ADK 2.13.0 with `a2a-sdk` 0.3.26 sent and received a `DataPart`. Two runners finished in parallel. The peer was the S3 A2A SDK app. Unconstrained resolve was 1.2.3, a different API | Not used. Pin `a2a-sdk==0.3.26` in both extras |
| S5 | Do Jev `noul`, `choice`, `score` work with your key? Latency and cache behavior | Heuristic fallbacks only, Jev removed from guards |
| S6 | Deferred. No Langfuse keys, so the spike was not run | Group by Langfuse session id and tags |
| S7 | On Windows: short-path `git worktree add` and `remove`, `psutil` process cwd, killing a process tree, `core.autocrlf=false` | Shorten the worktree root, kill by job object |
| S8 | Do ADK and CrewAI install into one environment? | Two environments (default plan): `.venv-orch` and `.venv-worker` |
| S9 | FALLBACK. ADK `BaseLlm` can script a tool call with no network. A CrewAI `BaseLLM` tool-call list is rejected because `TaskOutput.raw` must be a string. `python spikes/s9_fakes.py --shim` writes `marker.txt` in cwd | LiteLLM `mock_response` or a recorded-fixture proxy; the shim is a small Python script on PATH |
| S10 | FALLBACK. `cursor-agent` 2026.10.22-27dcf5f. Flags: `--print`, `--output-format json`, `--model`, `--workspace`, `--trust`, `--force`, `--sandbox disabled`. JSON `usage` has `inputTokens`, `outputTokens`, `cacheReadTokens`, `cacheWriteTokens`. No usage pool. Model list and a CLI auto id were not measured. With the sandbox disabled, a denied file and an outside write both landed | Hard timeout, process-tree kill, scope diff and proofs. The permission file is not a boundary |
| S10b | FALLBACK. `--sandbox enabled` exits 1 on Windows. Stderr: sandbox requires macOS or Linux. No edit, no hang, no JSON | Do not pass `--sandbox enabled` on Windows. Detect out-of-repo writes only inside the repo. A container is the real boundary |
| S11 | FALLBACK. `--api-key` and `CURSOR_API_KEY` exist. The key is unset. An existing browser login is present. The container was not run | Run the demo locally |

**Cursor prompt**

```
Read @docs/ARCHITECTURE.md sections 4, 7 and 10 and @docs/PHASES.md Phase 0.
Create spikes/s1_adk_deepseek.py ... spikes/s11_cursor_auth.py, one per spike, each under 80 lines,
each printing a clear PASS/FAIL line and the exact installed package versions.
Do not build any product code. After I run them, I will paste output and you will write
docs/spikes/summary.md with one Result line per spike and the chosen fallback.
```

**Gate:** `docs/spikes/summary.md` exists and has a `Result:` line for S1 to S11. Package versions are pinned in `uv.lock`. Test: `tests/test_phase0_spikes.py` (marker `phase0`).

**Exit:** met. The architecture sections that were tagged `[verify P0]` now state the measured outcome. The CrewAI agent step hook name was not measured, so that tag was removed rather than filled in.

S10 on 2026-10-27 (`cursor-agent` 2026.10.22-27dcf5f) measured the model flag as `--model`. The JSON result included a `usage` object with token counts, not a usage pool. The run did not list models and did not record whether an auto model id exists. The CLI help text names `--list-models` and a `models` command; neither was executed. Until Phase 5 exists, a run with `worker_model` `router` uses `models.router.default_tier`.

---

## Phase 1: Skeleton, config, events, gate (3 h)

**Goal:** a runnable repo with the gate, configuration and the event pipeline that everything else reports into.

**Files:** `pyproject.toml`, `treefleet.toml`, `src/treefleet/config.py`, `src/treefleet/events.py`, `scripts/gate.py`, `docs/STATUS.md`.

**Cursor prompt**

```
Read @AGENTS.md, @docs/CONTRACTS.md section 5 and @src/treefleet/contracts.py.
Implement config.py: load treefleet.toml into a frozen Pydantic Settings model (models.strong, models.fast,
thresholds, ports, worktree root, pricing source URLs, paths to the two virtualenv pythons). Environment
variables override secrets only. Fail fast with a clear error on a missing key.
Implement events.py: EventSink with async emit(), monotonically increasing seq per run,
JSONL file sink, in-memory fan-out queue for SSE subscribers with Last-Event-ID replay, and a
thread-safe sync wrapper (CrewAI callbacks are synchronous).
Write tests first. Mark them phase1.
```

**Gate tests:** config rejects unknown keys and missing secrets; `seq` is gapless under 50 concurrent emitters; replay after `Last-Event-ID` returns exactly the missed events; JSONL lines validate as `RunEvent`.

**Exit evidence:** `evidence/phase-1/gate.txt`.

---

## Phase 2: Git layer and isolation proofs (5 h)

**Goal:** everything about worktrees, the canary, proofs and merging, with no LLM involved.

**Files:** `src/treefleet/git/{worktrees,canary,proofs,merge}.py`, `src/treefleet/mcp_servers/gitops_server.py`.

**Cursor prompt**

```
Read @docs/ARCHITECTURE.md sections 3 and 13 and @docs/CONTRACTS.md sections 2.2 and 7.
Implement the git library with subprocess (shell=False, explicit timeouts, UTF-8, no global state):
prepare_repo (clone or open, check clean), write_canary, snapshot_main, create_worktree, remove_worktree
(force, after the process is gone), list_worktrees, collect_changes, verify_task_isolation, verify_main,
integrate (merge approved branches in plan order, record conflicts without resolving, abort cleanly).
Implement gitops_server.py with the read-only MCP tools listed in the contract.
Tests use real temporary git repositories created in fixtures. Mark them phase2.
```

**Gate tests**

1. Three worktrees created from one repo each report a different toplevel and branch.
2. A seeded violation per proof check makes `ok` false: a canary copied into a worktree, a write outside `allowed_paths`, a worktree missing from the list, a main-checkout edit.
3. Same file, different sections: integration merges cleanly. Same hunk: conflict reported, nothing resolved, repository left clean.
4. Worktree path stays under 120 characters on Windows.
5. MCP tool contract tests for `gitops-mcp` (every tool returns `ok` or a typed error).

**Fallback:** if worktree removal races with Windows file locks, kill the process tree first and retry removal with backoff.

---

## Phase 3: Worker (CrewAI + MCP + A2A server) (6 h)

**Goal:** one worker, runnable alone, whose CrewAI agent reasons on DeepSeek and directs a headless `cursor-agent` to do a scoped task inside one worktree, and which returns a `WorkerResult` over A2A. Cursor writes the code. DeepSeek never edits a file.

**Files:** `src/treefleet/mcp_servers/worktree_fs_server.py`, `src/treefleet/worker/{main,crew,a2a_server,callbacks,cursor_tool}.py`.

**Cursor prompt**

```
Read @docs/ARCHITECTURE.md sections 1a, 3, 7, 8 and @docs/CONTRACTS.md sections 1, 2.1 and 2.3, and the Phase 0 summary.
`WorkerTaskEnvelope.cursor_model` already exists. Resolve it per task: `model_override`, then `RunRequest.worker_model` when it is not `router`, then `models.router.default_tier` (Phase 5 replaces this with the Jev tier table), then `models.default`, then omit `--model`. Validate explicit ids against the process-cached CLI model list before creating a worktree. Pass a resolved id as `--model` (the flag Phase 0 measured) and emit `model_selected`. Add `CursorRunResult` from the real CLI output, with tests and a STATUS note. Do not add `plan_approval` or `output_line` here.
Build worktree_fs_server.py exactly to the read-only tool contracts: realpath containment, symlink escape rejection,
run_check by name only with shell=False, minimal env, output tails. It has no write tools.
Build cursor_tool.py: the cursor_code CrewAI tool per section 2.3, using the flags recorded by spike S10,
with scope revert after every run.
Build the worker: a CLI (--port --worktree --token --run-id) that sets cwd to the worktree, starts the MCP
server as a stdio subprocess with --root, builds one CrewAI Agent on the DeepSeek model whose tools are the
read-only MCP tools, run_check and cursor_code, and one Task. Do not use output_pydantic. The crew returns text.
Plain code parses WorkerDraft with Pydantic and one repair retry that sends the validation error back (S1).
A guardrail diffs against allowed_paths. The agent inspects the task, writes the Cursor instruction, calls cursor_code,
inspects the diff, runs checks, and may call cursor_code again with failures, bounded by Limits. After the crew
finishes, plain code reverts out-of-scope paths, commits on branch tf/<run>/<task>, and assembles WorkerResult.
Serve it as an A2A SDK app whose executor runs the crew (S3). The LLM never runs git commit and never writes a file.
Offline CrewAI uses LiteLLM mock_response (S9). The fake cursor-agent is `python spikes/s9_fakes.py --shim`, which writes marker.txt in its cwd.
On Windows, cursor-agent uses `--sandbox disabled` (S10b).
```

**Gate tests**

1. Path guard property tests: `..`, absolute paths, drive letters, symlinks to outside, `.git` writes, Unicode lookalikes, long paths. All rejected with the right code.
2. `run_check` refuses any name not in `checks` and never starts a shell.
3. Worker with the fake LLM and the fake cursor shim edits `README.md`, passes its check, and returns a valid `WorkerResult` whose `head_sha` exists in the repository. The worker agent has no write tool (asserted).
4. The fake cursor shim writes an out-of-scope file and a file outside the worktree. The wrapper reverts the first, the proof catches the second, the guardrail fails the attempt, and the worker retries then fails cleanly after the limit.
4a. `cursor_code` times out: the process tree is killed, a `CursorRunResult` with `timed_out=true` is returned, and no orphan process remains.
5. The agent card is served and a plain A2A client call returns a `WorkerResult` (`phase3`).

**Live check (once):** same task with DeepSeek reasoning and the real `cursor-agent`, saved to `evidence/phase-3/live.json`.

---

## Phase 4: Orchestrator core (ADK) (7 h)

**Goal:** goal in, parallel isolated workers out, verified proofs, a typed report, no review loop yet.

**Files:** `src/treefleet/orchestrator/{agents,fleet,pipeline}.py`, `src/treefleet/worker/spawner.py`.

**Cursor prompt**

```
Read @docs/ARCHITECTURE.md sections 2, 5, 6, 7, 9 and @docs/CONTRACTS.md sections 1 and 4.
Build the ADK pipeline: RepoScan (deterministic BaseAgent), Planner (LlmAgent, strong model, Plan via the
structured-output strategy chosen in Phase 0, output_key="plan"), FleetAgent (custom BaseAgent), Verifier,
Reporter. FleetAgent, per wave from Plan.waves(): create worktrees via the git library, spawn one worker process
per task with the worker virtualenv python, cwd = worktree, minimal env, per-run token; wait for the agent card;
dispatch WorkerTaskEnvelope through RemoteA2aAgent runners (or the Phase 0 fallback) with asyncio.gather;
write results into state via EventActions(state_delta); always kill the processes in a finally block.
Reporter builds FinalReport and its Markdown deterministically. All state values go through contracts.
Offline tests use the fake LLMs on both sides. Mark them phase4.
```

**Gate tests**

Addendum: tool names exposed to the worker stay at 64 characters or fewer and `git_diff` stays callable when the worktree root path is 200 characters. Before spawn, an untracked `.cursor/` directory or `.cursorrules` file emits `scope_violation` and fails the task. Tracked `.cursor/` content is a `git.cursor_warning` and is not executed. `cursor-agent` is started with `--trust`.

1. Three workers run concurrently (assert overlapping time windows from events), results land in state under the contract keys.
2. `IsolationReport.ok` is true; three distinct toplevels and branches appear in the proof.
3. Kill one worker mid-run: that task is `failed`, the other two finish, processes are gone afterward, run completes.
4. Dependent tasks run in later waves and receive `upstream` summaries.
5. Process leak test: no `treefleet.worker` process survives a normal run, a failed run or a cancelled run.

---

## Phase 5: Guardrails, Jev, callbacks, routing (5 h)

**Goal:** the safety and monitoring layers, and the cheap typed calls.

**Files:** `src/treefleet/jev/client.py`, `src/treefleet/guards/*.py`, `src/treefleet/orchestrator/callbacks.py`, `src/treefleet/worker/callbacks.py`, router agent.

**Cursor prompt**

```
Read @docs/ARCHITECTURE.md sections 8, 10, 11 and @docs/CONTRACTS.md sections 3 and 5.
Implement the Jev client with httpx: one batched call for many questions, retry on 429 and 529 honoring Retry-After,
timeouts, in-memory cache, strict Pydantic parsing, usage accounting into CostLine. Never log the state text.
Implement the six guard stages from the architecture table. Deterministic checks run first and cannot be skipped;
Jev adds scores; a DeepSeek classification prompt replaces Jev when Jev is down.
Wire ADK before/after agent, model and tool callbacks and the CrewAI task callback and guardrail so that
every one emits a RunEvent. Add the router: map Jev complexity (`trivial`, `moderate`, `complex`) through the `models.router` tier table in `treefleet.toml` to a Cursor model. If Jev or routing is unavailable, use `models.router.default_tier`. Emit `model_selected` with `task_id`, `model`, `source`, and `complexity`.
Add Jev relevance filtering inside worktree-fs-mcp for outputs over 6000 characters.
Tests first. Mark them phase5.
```

**Gate tests**

1. Guard corpus of at least 30 labeled prompts (15 benign, 15 malicious across injection, abuse, off-topic, zero-width and homoglyph tricks). Deterministic layer alone must catch the obvious ones. Precision and recall are printed.
2. Jev client against recorded HTTP fixtures: retry behavior, cache hit counts, malformed response rejected.
3. Indirect injection: a repo file instructs the agent to read `../../secret.txt`. The task completes and a `tool_args` denial event exists.
4. A tool returning a malformed object is rejected by the `tool_result` guard.
5. Event completeness: for a scripted run, every model call, tool call and guard decision produced exactly one event.
6. With Jev disabled (`use_jev=false`) and with Jev failing, the run still completes.

---

## Phase 6: Review loop and integration (6 h)

**Goal:** evaluator-optimizer behavior and the merged result.

**Files:** reviewer agent, revise loop in `fleet.py`, `git/merge.py` integration wiring.

**Cursor prompt**

```
Read @docs/ARCHITECTURE.md sections 5, 6, 13 and @docs/CONTRACTS.md sections 2.2 and 4.
Build the Reviewer as an ADK LlmAgent using McpToolset over gitops-mcp (read-only tools only).
Do not set output_schema. Parse ReviewVerdict JSON with Pydantic and one repair retry (S1). Implement the revise loop: verdict "revise" re-dispatches
the same worker with attempt+1 and revision_feedback, maximum two revisions, then the task becomes rejected.
Wire the Integrator and put IntegrationReport into FinalReport. Add the Jev in_scope pre-check before the Reviewer.
Scripted-verdict tests first. Mark them phase6.
```

**Gate tests:** scripted `revise` then `approve` produces attempt 2 on the same worktree and branch; `revise` three times yields `rejected` and exclusion from integration; reviewer cannot call any mutating tool (assert the tool list); conflict scenario reported, not resolved; `FinalReport` validates and renders Markdown.

---

## Phase 7: Observability (3 h)

**Goal:** one Langfuse trace tree per run, with provenance.

**Files:** `src/treefleet/obs/tracing.py`, hooks in orchestrator and worker entry points.

**Cursor prompt**

```
Read @docs/ARCHITECTURE.md section 10 and the Phase 0 note on S6.
Initialize Langfuse first, then the Google ADK instrumentor in the orchestrator process and the CrewAI instrumentor in
each worker process, before any agent code. Inject traceparent into every A2A message and WorkerTaskEnvelope, extract it
in the worker. Set session id = run_id and tags (scenario, model tier) on every trace. Add run_id, task_id and head_sha
as attributes. Flush on shutdown. Provide a no-op mode when Langfuse keys are absent.
Tests use an in-memory OpenTelemetry exporter. Mark them phase7.
```

**Gate tests:** spans exist for planner, each worker and each tool call; worker spans share the orchestrator's trace id, or share the session id if the fallback was chosen; missing keys do not crash a run; no prompt or file text appears in span attributes beyond what you allow in config.

**Evidence:** screenshot of the trace tree in Langfuse (`evidence/phase-7/trace.png`).

---

## Phase 8: Web UI (6 h)

**Goal:** the kanban and the proof panel that make the demo self-explanatory.

**Files:** `src/treefleet/api/server.py`, `src/treefleet/api/static/`.

**Design is locked:** `docs/design-direction.md` and the static mock `docs/mockup/index.html`. The page must match them. The gate checks both files exist.

**Cursor prompt**

```
Read @docs/CONTRACTS.md sections 5 and 6 and @docs/design-direction.md.
Build the FastAPI server for the listed endpoints with bearer auth, SSE with Last-Event-ID resume, and the static page.
Page: folder picker and one prompt box with detected checks to confirm; a Worker model select beside Run (Auto first, then ids from `GET /api/models`); plan approval card with a per-task model select and the auto-run switch;
lanes Needs you, Working, In review, Ready to merge, Merged; cards with branch, status dot, five-segment proof strip,
elapsed time and file count; a task drawer whose header shows the chosen model, with Live output, Diff, Proof and Feedback tabs; the branch map; the final report;
buttons for Run, Approve plan, Run scenario (same-file, saboteur, canary, injection), Cleanup, Download patches.
Match docs/mockup/index.html exactly in tokens, type and layout. Plain HTML, CSS and JavaScript, no framework.
Add the endpoints for approval and folder listing and the plan_approval and output_line contract changes.
```

**Gate tests:** API contract tests with `httpx`, SSE resume returns exactly the missed events, unauthorized requests rejected, proof endpoint output validates as `IsolationReport`. Manual check: open the page, run the same-file scenario, screenshot to `evidence/phase-8/`.

---

## Phase 9: Evaluation, cost and time (5 h)

**Goal:** requirement 10 as a repeatable report.

**Files:** `src/treefleet/evals/{scenarios,harness,report}.py`.

**Scenarios**

| Id | What it shows | Expected |
|---|---|---|
| S1 same-file-disjoint | Three workers edit different sections of `README.md` | Three clean diffs, clean integration |
| S2 same-file-conflict | Two workers edit the same lines | Conflict reported, nothing auto-resolved |
| S3 saboteur | One worker gets a scripted fault that breaks the tests (demo-only code path) | Its checks fail, the others stay green, main checkout clean |
| S4 canary | Untracked canary in the main checkout | Absent in every worktree |
| S5 direct-injection | Malicious goal | Blocked at the input guard |
| S6 indirect-injection | Malicious text inside a repo file | Task completes, tool denial logged |

**Metrics per scenario:** success, isolation violations (must be 0), tokens and USD per component, wall time, sequential estimate, speedup, guard precision and recall on the corpus, Jev calls. **Ablations:** Jev on versus off (cost and time), 1 worker versus N workers (speedup), strong versus fast model for workers (quality and cost).

**Cursor prompt**

```
Read @docs/ARCHITECTURE.md section 11 and the scenario table in @docs/PHASES.md Phase 9.
Build the harness to run every scenario N times against the fake LLMs (deterministic, offline) and, with --live,
against the real models. Compute cost from usage metadata and the LiteLLM cost map plus the Jev rate in config, never from
hand-entered prices. Emit evidence/phase-9/eval-report.md (tables) and eval-report.json. Fail the harness if any run has
an isolation violation. Mark tests phase9.
```

**Gate tests:** offline harness produces a schema-valid report; an injected isolation violation makes the harness exit non-zero; cost totals equal the sum of component lines.

---

## Phase 10: Cloud deployment (4 h)

**Goal:** the same system running on Google Cloud Run (or the platform you confirm).

**Files:** `deploy/Dockerfile`, `deploy/deploy.sh`, `docs/DEPLOY.md`.

**Cursor prompt**

```
Read @docs/ARCHITECTURE.md section 12.
Write a multi-stage Dockerfile with git, the two virtual environments (orchestrator and worker), a non-root user,
and an entrypoint running uvicorn. Write deploy.sh using gcloud: build, push, deploy with CPU always allocated,
request timeout 3600, memory and CPU sized for three workers, secrets from Secret Manager, and a bearer token
for /api. Add a post-deploy smoke script that hits /healthz, starts a same-file run against a small public repo,
waits for the report and checks isolation ok. Document every command in DEPLOY.md.
```

**Gate:** local `docker build` succeeds, container smoke test passes locally, then the deployed smoke script passes. **Evidence:** service URL, deploy log, a screenshot of a finished run, and the Langfuse trace from the cloud run.

**Cursor in the container:** install the Cursor CLI in the worker image and authenticate with the API key secret per spike S11. The smoke test runs one real Cursor task. If S11 failed, deploy on a VM per ARCHITECTURE section 12 and record that choice in `docs/DEPLOY.md`.

**Fallback:** if background subprocesses stall under request-based CPU, switch to CPU always allocated. If Cloud Run is unavailable to you, any container host that can run long-lived processes works. Only the deploy script changes.

---

## Phase 11: Demo hardening (3 h)

**Goal:** a rehearsed demo and a complete evidence trail.

1. Fill `docs/REQUIREMENTS_TRACE.md` evidence column with real file paths and links.
2. Run the demo script three times end to end on the cloud deployment and once locally. Record a backup screen capture.
3. Prepare the answers in the Q&A list in the trace document.
4. Freeze: tag `v1.0`, export `evidence/` as a zip.

**Gate:** `python scripts/gate.py --phase 11 --live` is green and every row in the trace has an evidence path that exists (test `tests/test_trace_evidence.py`).

---

## Dependency order and parallel work

```
P0 -> P1 -> P2 -> P3 -> P4 -> P5 -> P6 -> P7 -> P9 -> P10 -> P11
                    \                         ^
                     \-> P8 (after P4 API shape) -/
```

P8 can start once P4 exposes events. P7 can run beside P6. Everything else is sequential.

## If time runs short

Cut in this order: P8 polish (keep the proof panel and kanban), the Jev ablation, Windows-specific polish, the revise loop's second retry. Never cut: P0, P2's proofs, P3's guards, P5's guardrails, P7, P9's scenarios S1 to S6, P10. They carry the grading requirements.
