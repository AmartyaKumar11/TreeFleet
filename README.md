# TreeFleet

TreeFleet runs one coding goal as several tasks in parallel. An ADK orchestrator plans the work. Each task gets its own git worktree and a CrewAI worker that drives a headless Cursor CLI agent. Orchestrator and workers talk over A2A. Isolation is proved by deterministic git and process checks, not by what a worker claims.

## Architecture (ten lines)

1. A goal enters through the CLI or the local UI.
2. An input guard can block the run before any worktree exists.
3. The ADK planner writes a typed `Plan` of tasks with path scopes.
4. A router picks a Cursor model tier (Jev when available, else a config default).
5. FleetAgent creates one worktree and one worker process per ready task.
6. Each worker is a CrewAI agent behind an A2A server; it directs `cursor-agent` in that worktree.
7. After the wave, plain code computes isolation proofs against scope, canary, and sibling trees.
8. An ADK reviewer approves, revises (max twice), or rejects each finished task.
9. Approved branches merge into `tf/<run>/integrated`; conflicts are reported, not auto-resolved.
10. `FinalReport` lands as JSON and Markdown; every stage also appends to `events.jsonl`.

Details: `docs/ARCHITECTURE.md`, contracts in `src/treefleet/contracts.py`.

## Install

Python 3.12. Two virtualenvs on purpose (ADK and CrewAI must not share a process or a dependency set):

```
uv venv .venv-orch && uv pip install -p .venv-orch -e ".[orchestrator,dev]"
uv venv .venv-worker && uv pip install -p .venv-worker -e ".[worker]"
```

Activate the orchestrator env for CLI, UI, gate, and eval. Workers are spawned with `.venv-worker`.

## Environment variables

Set these in the environment (never in git). Do not print values.

| Name | Role |
|---|---|
| `DEEPSEEK_API_KEY` | Required. Reasoning models for planner, worker brain, reviewer |
| `TYPESAFE_API_KEY` | Required. Jev classification |
| `CURSOR_API_KEY` | Optional. Passed into the Cursor CLI child when set |
| `LANGFUSE_PUBLIC_KEY` | Optional. Observability (phase 7 deferred) |
| `LANGFUSE_SECRET_KEY` | Optional. Observability (phase 7 deferred) |
| `LANGFUSE_BASE_URL` | Optional. Observability (phase 7 deferred) |

Non-secret config lives in `treefleet.toml`.

## Run the CLI

From the TreeFleet repo root, with `.venv-orch` active and a target git repo:

```
treefleet "Add unit tests for src/notifications.py" --repo D:\pr-sentinel-demo --yes
```

Useful flags: `--model router` (default), `--no-approval`, `--saboteur TASK` (demo only), `--check name=cmd`. Cleanup: `treefleet cleanup <run_id> --repo <path>`.

## Run the UI

```
treefleet ui --repo D:\pr-sentinel-demo
```

Board listens on `127.0.0.1:8765`. Replay a recording with `--replay evidence/phase-8/events.jsonl --port 8766`.

## Demo

Follow `docs/DEMO.md`: preflight, reset, open the board and worktree watcher, paste the two-task goal, narrate stages, fall back to replay if a live stage stalls.

## Gate and eval

Cumulative offline gate for phases 0 through N (lint, types, tests; excludes `@pytest.mark.live`):

```
python scripts/gate.py --phase 9
```

Add `--live` only when you intend to hit real APIs. One phase: `python -m pytest -q -m "phase9"`.

Eval harness (live scenarios against a clean demo repo):

```
treefleet eval --repo D:\pr-sentinel-demo
```

Writes `eval/report.md` and copies into `evidence/phase-9/`.

## Evidence and requirements

- Evidence index: `evidence/INDEX.md`
- Requirements trace: `docs/REQUIREMENTS_TRACE.md`
- Status by phase: `docs/STATUS.md`
