# TreeFleet: instructions for Cursor

TreeFleet is Agent Orchestrator rebuilt on ADK and CrewAI. It runs one goal as several coding tasks in parallel. An ADK orchestrator plans, a CrewAI worker per task directs a headless Cursor CLI agent that edits inside its own git worktree, and the two talk over A2A. Isolation is proven by deterministic code, never by model claims.

Read before coding: `docs/ARCHITECTURE.md`, `docs/CONTRACTS.md`, the current phase in `docs/PHASES.md`. Contracts live in `src/treefleet/contracts.py`.

## Hard rules

1. **Frameworks:** only Google ADK and CrewAI are agentic frameworks. Do not add LangChain, LangGraph, AutoGen, LlamaIndex, Semantic Kernel or similar. Libraries such as LiteLLM, the A2A SDK, the MCP SDK, httpx, FastAPI and Pydantic are fine.
2. **No LLM in git or isolation.** Worktree creation, process spawning, commits, merges, proofs and path guards are plain Python.
3. **Contracts only.** Every cross-component payload is a model from `contracts.py`. Change a contract only with a test and a note in `docs/STATUS.md`. Never pass raw dicts across a boundary.
4. **Never trust a worker.** Anything in `WorkerResult.isolation` is a claim. Proofs come from `treefleet.git.proofs`.
5. **Subprocess hygiene:** `shell=False`, explicit timeout, explicit `cwd`, minimal `env`, UTF-8. No `os.system`, no `shell=True`.
6. **Workers get a minimal environment:** the DeepSeek key, the per-run token and the Cursor credential, nothing else. Never log secrets or prompt and file contents.
7. **Hub and spoke.** Workers never address each other. Cross-task data moves through the orchestrator.
8. **Two Python environments:** `.venv-orch` (ADK) and `.venv-worker` (CrewAI). Code under `src/treefleet/worker/` must not import `google.adk`. Code under `src/treefleet/orchestrator/` must not import `crewai`. Shared code (`contracts.py`, `jev/`, `git/`, `guards/`, `events.py`, `config.py`) imports neither.
9. **Windows and Linux both.** Use `pathlib`, no POSIX-only calls without a guard, worktree root stays short.
10. **Fail closed on safety, fail open on cost.** A guard error blocks. A Jev error falls back.
11. **Cursor writes the code, DeepSeek reasons, Jev classifies.** No DeepSeek or Jev output is ever written to a file. The CrewAI worker agent has no write tool. The only way a file changes is `cursor_code` launching `cursor-agent` with cwd set to the worktree. After every run, plain code reverts out-of-scope paths. Tests use the fake `cursor-agent` shim, never the real CLI, unless marked `live`.
12. **Cursor credential** is the one extra secret a worker gets (`CURSOR_API_KEY`, if spike S11 confirms that mechanism). Never log it or put it in a prompt.

## Commands

```
uv venv .venv-orch   && uv pip install -p .venv-orch   -e ".[orchestrator,dev]"
uv venv .venv-worker && uv pip install -p .venv-worker -e ".[worker]"
python scripts/gate.py --phase N            # lint, types, tests for phases 0..N
python scripts/gate.py --phase N --live     # also runs tests that call real APIs
python -m pytest -q -m "phase3"             # one phase
```

## Conventions

- Python 3.12, full type hints, `from __future__ import annotations`, US spelling.
- Tests first. A phase is done only when its gate is green and its evidence exists under `evidence/phase-N/`.
- Never weaken, skip or delete a test to get green. Fix the cause.
- Offline tests use scripted fake LLMs. Tests that call real APIs carry `@pytest.mark.live`.
- No TODOs, stubs or placeholder values in committed code. If something is not ready, it is not committed.
- Event emission is mandatory in every callback. See `docs/CONTRACTS.md` section 5.
- Configuration lives in `treefleet.toml`. Secrets come only from environment variables: `DEEPSEEK_API_KEY`, `TYPESAFE_API_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`.
- Model names, prices and thresholds are config, not constants.

## When stuck

After three failed attempts at the same red test, stop and write `docs/spikes/<phase>-<topic>.md` with what failed, what you learned and the options. Then pick the fallback named in `docs/PHASES.md` or propose an architecture change.
