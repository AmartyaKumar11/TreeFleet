# Status

Spec complete. No product code beyond `contracts.py` exists yet.

| Phase | Name | State | Evidence |
|---|---|---|---|
| 0 | Spikes | done | `docs/spikes/summary.md` |
| 1 | Skeleton, config, events, gate | contracts done and tested | `tests/test_contracts.py` |
| 2 | Git layer and isolation proofs | not started | |
| 3 | Worker (CrewAI, MCP, A2A server) | not started | |
| 4 | Orchestrator core (ADK) | not started | |
| 5 | Guardrails, Jev, callbacks, routing | not started | |
| 6 | Review loop and integration | not started | |
| 7 | Observability | not started | |
| 8 | Web UI | not started | |
| 9 | Evaluation, cost, time | not started | |
| 10 | Cloud deployment | not started | |
| 11 | Demo hardening | not started | |

## 2026-10-27 — Phase 0 gate

Ruff excludes `.venv*` and `spikes/`. Those are local environments and throwaway measurement scripts, and they do not ship. Pyright runs with `--pythonpath` pointed at `.venv-orch` over `src`, `tests`, and `scripts`, skipping `src/treefleet/worker`. A second run uses `.venv-worker` on that folder only, and only once the folder exists. Spikes and the venv folders are excluded from both.

## 2026-10-27 — Phase 0

Spikes S1 through S5 and S7 through S11 are recorded in `docs/spikes/`. S6 is deferred (no Langfuse keys). S10b: `--sandbox enabled` does not start on Windows, because the CLI requires macOS or Linux. With `--sandbox disabled`, S10 blocked neither a denied file nor an outside write. DeepSeek ids are `deepseek-flash` and `deepseek-v4-pro` through LiteLLM. ADK 2.13.0 and CrewAI 1.15.30. Both extras pin `a2a-sdk==0.3.26`. Workers are A2A SDK apps. Planner, reviewer, and `WorkerDraft` JSON are parsed with Pydantic. `output_schema` and CrewAI `output_pydantic` are not used. The demo runs locally. `treefleet.toml` does not exist yet, so model names were not edited there.

## Contract changes

- 2026-10-27. Worker model selection. `ModelChoice` matches `^[A-Za-z0-9._:/\-]{1,100}$`. `RunRequest.worker_model` defaults to `router` (UI and CLI label Auto). `TaskSpec.model_override` and `WorkerTaskEnvelope.cursor_model` are optional Cursor model ids. The envelope `model` field stays the DeepSeek reasoning model. `RouteDecision` gains `cursor_model`. Its `source` also accepts `override`, `run`, `router`, and `default`. Older values `jev` and `heuristic` still validate. `model_selected` is in the event vocabulary. `CursorRunResult`, `plan_approval`, and `output_line` stay scheduled for Phases 3 and 8. S10 measured `--model` and a token `usage` object. It did not list models, record a CLI auto id, or a usage pool. Until Phase 5, a `router` run uses `models.router.default_tier`.

## Architecture change: Cursor CLI as the coding engine
- Workers are CrewAI agents on DeepSeek that direct a headless `cursor-agent` in each worktree. Cursor writes all code. DeepSeek never edits files. Jev classifies.
- New spikes S10 (headless Cursor in a worktree) and S11 (non-interactive auth, container). Phase 0 gate now covers S1 to S11.
- `WorkerTaskEnvelope.cursor_model` is on the contract (2026-10-27). Still pending in Phase 3: `CursorRunResult`. `worktree-fs-mcp` is read-only.

## Design locked
- Dark, AO-style board with Cursor-like surfaces. Details in `docs/design-direction.md`, static mock in `docs/mockup/index.html`.
- Folder-first CLI and UI, auto-detected checks with confirmation, plan approval on by default with an auto-run switch, read-only live output.
- Pending contract changes: `RunRequest.plan_approval`, `output_line` event kind (Phase 3 and 8).
