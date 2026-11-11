# TreeFleet

Parallel coding agents in isolated git worktrees, with a pull-request, CI, and review feedback loop. **Agent frameworks: only Google ADK and CrewAI.**

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

| Desktop — Done (run `aa68e041`, both PRs merged) | Mobile |
|---|---|
| ![Board with both tasks in Done after PR merge](docs/img/hero-board-done-1440.png) | ![Mobile Done board](docs/img/hero-board-done-390.png) |

Captured with **Playwright** against a fixture built from recorded run `aa68e041` (no browser chrome).

## Contents

- [Status](#status)
- [Why TreeFleet](#why-treefleet)
- [Feature overview](#feature-overview)
- [Architecture](#architecture)
- [Design decisions](#design-decisions)
- [Security model](#security-model)
- [Cost and performance (measured)](#cost-and-performance-measured)
- [Quickstart](#quickstart)
- [Configuration reference](#configuration-reference)
- [Repository layout](#repository-layout)
- [Testing strategy](#testing-strategy)
- [Platform support](#platform-support)
- [Limitations and known issues](#limitations-and-known-issues)
- [Roadmap](#roadmap)
- [Inspired by](#inspired-by)
- [Documentation map](#documentation-map)
- [Contributing and security](#contributing-and-security)
- [License](#license)

---

## Status

**Pre-1.0, research-grade.** The cumulative offline gate is **gate 11** (`python scripts/gate.py --phase 11`). It includes onboarding (`treefleet init` / `doctor` / `try`), optional-Jev, and routing tests. Last recorded pass: **406** tests (2026-11-11). That gate and the unit suite are the contract of correctness. **Verified with real models** in recorded runs under `evidence/` (planner, workers, Cursor, reviewer, isolation proofs). **Not production-hardened:** no hosted multi-tenant control plane, Langfuse wiring is deferred (phase 7), and Windows Cursor runs without a sandbox—scope revert and proofs are the boundary.

| Area | Verified live | Offline / faked |
|---|---|---|
| Isolation proofs | `evidence/phase-2/`, phase-6 live runs | Windows sibling/main mutation tests |
| ADK orchestrator + A2A workers | `evidence/phase-4/` … `phase-6/` | Scripted `BaseLlm` in gate |
| Guards + Jev | `evidence/phase-5/guard-demo/` | Respx / scripted Jev |
| Web UI board | `evidence/phase-8/` | Playwright harness |
| Eval harness | `eval/report.md`, `evidence/phase-9/` | E5 forced-plan hook |
| PR loop (`gh`) | `evidence/phase-11/`, `evidence/phase-12/rehearsal/` | `FakeProvider` in tests |

---

## Why TreeFleet

Parallel agent products often share one checkout or trust workers to stay in scope. That makes races likely and “isolation” a claim in a log line. Code review, CI, and agent output are usually separate channels, so a failing check or a trusted PR comment does not automatically become structured revision work on the right branch.

TreeFleet treats **one goal → many tasks → many branches** as the default shape. Each task runs in its own **git worktree** and **OS process**. The orchestrator never lets workers talk to each other; cross-task data flows only through typed contracts (`src/treefleet/contracts.py`). **Isolation is re-computed** from git and the process tree after every wave—not from `WorkerResult.isolation`. When `[pr]` is enabled, approved tasks become real pull requests; the same reaction table drives check failures, trusted comments, conflicts, and provider errors.

---

## Feature overview

| Capability | What it does | Where |
|---|---|---|
| Typed contracts | Every cross-boundary payload is Pydantic | `src/treefleet/contracts.py` |
| Input + plan guards | Deterministic rules, then Jev scores | `src/treefleet/guards/` |
| ADK pipeline | Scan → plan → route → fleet → verify → review → integrate/PR → report | `src/treefleet/orchestrator/` |
| Hub-and-spoke fleet | Spawns workers, A2A dispatch, kills on shutdown | `src/treefleet/orchestrator/fleet.py` |
| CrewAI worker | DeepSeek brain, read-only MCP, `cursor_code` only writer | `src/treefleet/worker/` |
| Isolation proofs | cwd, toplevel, worktree list, canary, scope, main HEAD | `src/treefleet/git/proofs.py` |
| Review loop | approve / revise / reject; separate check-revise budget | `src/treefleet/orchestrator/review.py` |
| Reactions | Single `resolve()` entry; capped retries | `src/treefleet/reactions.py`, `docs/REACTIONS.md` |
| PR lifecycle | open, poll checks, trusted comments, acceptance | `src/treefleet/pr/`, `docs/PR_LIFECYCLE.md` |
| Board + SSE | Lanes, proof drawer, cost tab from `events.jsonl` | `src/treefleet/ui/` |
| Sessions | Heartbeat, kill, cleanup; no resume | `src/treefleet/sessions.py`, `docs/SESSIONS.md` |
| Cost lines | Per planner/worker/cursor/reviewer/jev row in report | `src/treefleet/guards/cost.py` |
| Gate | Ruff + pyright + cumulative pytest phases | `scripts/gate.py` |

---

## Architecture

### System context

Trust boundaries: the **operator UI/CLI** starts runs; the **orchestrator** holds secrets for planning/review; each **worker** gets a minimal env (model key, per-run A2A token, optional Cursor credential); **Cursor** is untrusted and may write only via its CLI in the worktree cwd.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'background': '#ffffff', 'primaryColor': '#e2e8f0', 'primaryTextColor': '#0f172a', 'primaryBorderColor': '#334155', 'lineColor': '#334155', 'secondaryColor': '#f1f5f9', 'tertiaryColor': '#ffffff', 'clusterBkg': '#f1f5f9', 'clusterBorder': '#334155', 'edgeLabelBackground': '#ffffff', 'signalColor': '#334155', 'actorBorder': '#334155'}}}%%
flowchart TB
  subgraph user_trust["Trust: operator machine"]
    U[Operator / browser]
    CLI[CLI]
    UI[Web UI board]
  end

  subgraph orch_trust["Trust: orchestrator process"]
    ORCH[ADK pipeline]
    G1[Input guard]
    RS[Repo scan]
    PL[Planner]
    PG[Plan guard]
    RT[Router + Jev]
    FL[Fleet]
    VF[Isolation verifier]
    RV[Reviewer]
    IG[Integrator or PR stage]
    RP[Reporter]
    ORCH --> G1 --> RS --> PL --> PG --> RT --> FL --> VF --> RV --> IG --> RP
  end

  subgraph worker_trust["Trust boundary: one OS process per task"]
    W1[Worker + CrewAI]
    W2[Worker + CrewAI]
    CU1[cursor-agent cwd = worktree]
    CU2[cursor-agent cwd = worktree]
    MCP1[worktree-fs-mcp read-only]
    W1 --> MCP1
    W1 --> CU1
    W2 --> CU2
  end

  subgraph external["External services"]
    GH[GitHub via gh]
    DS[DeepSeek API]
    JV[Jev API]
    CR[Cursor API]
  end

  GIT[(Git: main checkout + worktrees)]

  U --> UI
  U --> CLI
  UI --> ORCH
  CLI --> ORCH
  FL -. A2A JSON-RPC .-> W1
  FL -. A2A JSON-RPC .-> W2
  ORCH --> GIT
  W1 --> GIT
  W2 --> GIT
  IG --> GH
  PL --> DS
  RV --> DS
  W1 --> DS
  RT --> JV
  CU1 --> CR
  CU2 --> CR
```

<details>
<summary>SVG fallbacks</summary>

If Mermaid does not render in your viewer, use the checked-in SVGs (generated with `@mermaid-js/mermaid-cli`):

| Diagram | SVG |
|---|---|
| System context | [context.svg](docs/img/diagrams/context.svg) |
| Run sequence | [sequence-run.svg](docs/img/diagrams/sequence-run.svg) |
| Isolation | [isolation.svg](docs/img/diagrams/isolation.svg) |
| PR lifecycle | [pr-lifecycle.svg](docs/img/diagrams/pr-lifecycle.svg) |
| Board lanes | [lanes.svg](docs/img/diagrams/lanes.svg) |

Sources: `docs/img/diagrams/*.mmd`
</details>

### Run sequence (integrate or PR mode)

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'background': '#ffffff', 'primaryColor': '#e2e8f0', 'primaryTextColor': '#0f172a', 'primaryBorderColor': '#334155', 'lineColor': '#334155', 'secondaryColor': '#f1f5f9', 'tertiaryColor': '#ffffff', 'clusterBkg': '#f1f5f9', 'clusterBorder': '#334155', 'edgeLabelBackground': '#ffffff', 'signalColor': '#334155', 'actorBorder': '#334155'}}}%%
sequenceDiagram
  autonumber
  actor Op as Operator
  participant UI as Web UI
  participant Orch as ADK orchestrator
  participant W as CrewAI worker
  participant Cur as cursor-agent
  participant GH as GitHub
  participant Rev as Pre-PR reviewer
  participant Acc as Acceptance judge

  Op->>UI: Goal + approve plan
  UI->>Orch: RunRequest
  Orch->>Orch: Input + plan guards
  Orch->>Orch: Create worktrees tf/run/task
  Orch->>W: A2A WorkerTaskEnvelope
  W->>Cur: cursor_code in worktree
  Cur-->>W: JSON usage + edits
  W->>W: run_check, commit on branch
  W-->>Orch: WorkerResult
  Orch->>Orch: IsolationProof + MainCheckoutProof
  Orch->>Rev: Review diff
  Rev-->>Orch: approve / revise / reject
  opt revise within budget
    Orch->>W: revision envelope
    W->>Cur: cursor_code
    W-->>Orch: WorkerResult
  end
  Orch->>Orch: git push task branch
  Orch->>GH: open_pr via gh
  loop poll
    Orch->>GH: get_pr + list_comments + checks
    alt trusted PR comment
      Orch->>W: revision envelope
      W->>Cur: cursor_code
      W->>W: commit on branch
      W-->>Orch: WorkerResult
      Orch->>Orch: git push task branch
      Orch->>GH: post_comment TreeFleet revision
      Orch->>Acc: acceptance on diff vs comment
      Acc-->>Orch: satisfied / unsatisfied
    else checks green + quiet
      Orch->>Orch: pr_status approved_green
    end
  end
  Op->>Orch: treefleet pr merge
  Orch->>GH: merge via gh (unless auto_merge)
```

Push, `open_pr`, revision comments, and merge run in **orchestrator Python** (`src/treefleet/pr/stage.py`, `loop.py`), not in workers. Acceptance uses `acceptance_judge` in `src/treefleet/orchestrator/review.py`. Deeper narrative: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/PR_LIFECYCLE.md`](docs/PR_LIFECYCLE.md).

![Run sequence (rendered)](docs/img/diagrams/sequence-run.svg)

### Worktree isolation model

- **Main checkout** — untouched index/HEAD; `.treefleet_canary` proves workers did not read it as their root.
- **Per task** — `git worktree add <worktree-root>/<run_id>/<task_id> -b tf/<run_id>/<task_id>`.
- **Branch naming** — `tf/<run_id>/<task_id>` (integration: `tf/<run_id>/integrated` when not in PR mode).

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'background': '#ffffff', 'primaryColor': '#e2e8f0', 'primaryTextColor': '#0f172a', 'primaryBorderColor': '#334155', 'lineColor': '#334155', 'secondaryColor': '#f1f5f9', 'tertiaryColor': '#ffffff', 'clusterBkg': '#f1f5f9', 'clusterBorder': '#334155', 'edgeLabelBackground': '#ffffff', 'signalColor': '#334155', 'actorBorder': '#334155'}}}%%
flowchart LR
  subgraph main["Main checkout"]
    MHEAD[HEAD + index]
    CAN[.treefleet_canary]
  end

  subgraph wt["Worktree tf/run/task"]
    BR[branch tf/run/task]
    IDX[own index + files]
    PROC[worker + cursor cwd]
  end

  P1[process cwd matches worktree]
  P2[rev-parse toplevel]
  P3[worktree list registration]
  P4[canary absent in worktree]
  P5[diff within allowed_paths]

  PROC --> P1
  BR --> P2
  BR --> P3
  CAN -. monitor .-> main
  CAN --> P4
  IDX --> P5
```

### PR lifecycle and reactions

When `[pr] enabled = true`, tasks move through poll-driven states. Lane labels on the board are derived from the **latest** `pr_status` and related facts ([`src/treefleet/ui/lanes.py`](src/treefleet/ui/lanes.py)).

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'background': '#ffffff', 'primaryColor': '#e2e8f0', 'primaryTextColor': '#0f172a', 'primaryBorderColor': '#334155', 'lineColor': '#334155', 'secondaryColor': '#f1f5f9', 'tertiaryColor': '#ffffff', 'clusterBkg': '#f1f5f9', 'clusterBorder': '#334155', 'edgeLabelBackground': '#ffffff', 'signalColor': '#334155', 'actorBorder': '#334155'}}}%%
stateDiagram-v2
  direction TB
  [*] --> task_done: worker commits
  task_done --> awaiting_prs: PR opened
  awaiting_prs --> checks_pending: CI running
  checks_pending --> checks_green: all required checks pass
  checks_pending --> revision: checks_failed reaction
  awaiting_prs --> revision: trusted pr_comment
  revision --> checks_pending: push new head
  checks_green --> needs_review: comment unsatisfied or worker failure
  checks_green --> approved_green: quiet + reviewer acceptance
  approved_green --> merged: human merge or auto_merge
  awaiting_prs --> closed: PR closed unmerged
  merged --> [*]
  closed --> [*]
  needs_review --> revision: retry armed
  needs_review --> needs_human: rounds exhausted
```

![PR lifecycle (rendered)](docs/img/diagrams/pr-lifecycle.svg)

| Event | Default action | retries | Notes |
|---|---|---|---|
| `checks_failed` | `send_to_agent` | 1 | Separate from reviewer revise budget |
| `review_revise` | `send_to_agent` | 1 (max 2 via `[revise]`) | `round_budget_s` default 240 |
| `review_rejected` | `needs_human` | 0 | Cannot be `ignore` |
| `merge_conflict` | `send_to_agent` | 1 | One resolve round per task |
| `provider_error` | `send_to_agent` | 3 | Escalate model after attempt 2 |
| `task_failed` | `needs_human` | 0 | |
| `needs_review` | `notify` | 0 | |
| `approved_green` | `ignore` | 0 | PR mode may notify |
| `run_done` | `notify` | 0 | |

Full inventory: [`docs/REACTIONS.md`](docs/REACTIONS.md). Hard cap **5** attempts per reaction key; guards and proofs are not reaction keys.

### Board lanes

| Lane | Primary facts | Rule of thumb |
|---|---|---|
| **Working** | `worker_active`, `fleet_in_progress`, `pr_revising` | Agent or PR revision in flight |
| **Needs you** | `needs_human`, `reject`, failed checks (non-PR), `cancelled` | Latest decision wins over stale approve |
| **In review** | `pr_open` without green, `review_started` | Awaiting checks or reviewer |
| **Ready to merge** | `pr_status == approved_green` or approve + quiet | PR mode needs green + acceptance |
| **Done** | `pr_status == merged` or integrated flow complete | |

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'background': '#ffffff', 'primaryColor': '#e2e8f0', 'primaryTextColor': '#0f172a', 'primaryBorderColor': '#334155', 'lineColor': '#334155', 'secondaryColor': '#f1f5f9', 'tertiaryColor': '#ffffff', 'clusterBkg': '#f1f5f9', 'clusterBorder': '#334155', 'edgeLabelBackground': '#ffffff', 'signalColor': '#334155', 'actorBorder': '#334155'}}}%%
flowchart TD
  F[facts from events + run.json] --> D{derive_lane}
  D -->|merged| DONE[Done]
  D -->|cancelled / needs_human / reject| NEEDS[Needs you]
  D -->|pr_revising / active worker| WORK[Working]
  D -->|pr open not green / reviewing| REV[In review]
  D -->|approve + approved_green| READY[Ready to merge]
  D -->|default in-flight| WORK
```

---

## Design decisions

| Topic | Why |
|---|---|
| **ADK + CrewAI only** | One orchestrator framework and one worker framework; A2A is the seam. No LangChain graph in the hot path ([`AGENTS.md`](AGENTS.md)). |
| **Hub-and-spoke** | Workers never address each other; every dependency is explicit in the plan and logged. |
| **Cursor writes, DeepSeek reasons** | Files change only when `cursor_code` launches `cursor-agent` with `cwd` = worktree; the CrewAI agent has no write tool. |
| **Proofs, not claims** | `IsolationProof` is computed in Python; worker-reported cwd is evidence, not verdict. |
| **Single `resolve()`** | All automated reactions go through one function with a 5-attempt ceiling. |
| **Two revise budgets** | Failing checks and reviewer `revise` each have their own retry counters ([`docs/REACTIONS.md`](docs/REACTIONS.md)). |
| **Untrusted diffs and comments** | Reviewer sees redacted diffs; PR comments pass through the input guard and `<untrusted-data>` wrapping (1800-char cap). |
| **Revision acceptance** | A trusted comment stays open until a reviewer accepts the new diff against that comment ([`docs/PR_LIFECYCLE.md`](docs/PR_LIFECYCLE.md)). |
| **No session resume** | `run.json` + `events.jsonl` support inspect and cleanup, not mid-task replay ([`docs/SESSIONS.md`](docs/SESSIONS.md)). |
| **Human merge by default** | `merge()` runs only when `auto_merge` is true or the operator runs `treefleet pr merge`. |

---

## Security model

| Threat | Control | Reference |
|---|---|---|
| Untrusted PR comment | `trusted_commenters`, guard + wrap, stale/own filters | `docs/PR_LIFECYCLE.md` |
| Malicious diff text | Reviewer prompts treat diff as data; redaction in events | `src/treefleet/guards/text.py` |
| Runaway worker | Per-step timeouts, process-tree kill, Windows Job Object kill-on-close | `docs/SESSIONS.md` |
| Scope escape | Post-cursor scope diff + revert; proof failures fail the task | `src/treefleet/git/cursor_scope.py` |
| Force push / wrong remote | Single refspec `tf/...`; `origin` must match `pr.repo` | `docs/PR_LIFECYCLE.md` |
| Merge without human | `auto_merge` default false | `src/treefleet/pr/config.py` |

---

## Cost and performance (measured)

**Method.** Dollar figures come from `metrics.costs` / `total_usd` in each run’s `report.json`, built by `src/treefleet/guards/cost.py` using rates in `treefleet.toml` `[pricing]` (fetched 2026-10-27 from DeepSeek and Cursor docs). Cursor rows are labeled **estimate** where the CLI usage is mapped to published Composer rates.

**Worker brain tokens:** CrewAI/LiteLLM often report empty usage. Every **total USD** below **excludes worker-brain DeepSeek tokens (not reported by the framework)** unless a future report row records them. Worker-brain DeepSeek tokens are not recorded in report.json, so these totals understate real spend. Full rehearsal breakdown: [`evidence/phase-13/freeze/aa68e041-record.md`](evidence/phase-13/freeze/aa68e041-record.md).

**Pricing inputs (configurable).** DeepSeek flash/pro per-million fields, Composer 2.5 / Fast fields, `jev_usd_per_million_input`. See `[pricing]` in `treefleet.toml` and vendor pages linked there.

### Per-run totals (recorded reports)

| Run ID / label | Tasks | Wall (s) | Fleet wall (s) | Total USD | Source |
|---|---|---:|---:|---:|---|
| `aa68e041` rehearsal-3 | 2 | 746.5 | 262.3 | 0.115 | `evidence/phase-12/rehearsal-3/report-metrics.json` |
| `d89593f5` clean live | 3 | 434.7 | 170.5 | 0.085 | `evidence/phase-6/live-clean/report.json` |
| UI live-real | 2 | 257.7 | 218.8 | 0.077 | `evidence/phase-8/ui-v2/live-real/report.json` |
| E2#1 eval | 3 | 249.6 | 192.6 | 0.119 | `eval/report.md` |
| E2#2 eval | 3 | 515.2 | 212.6 | 0.190 | `eval/report.md` |

Each **Total USD** excludes worker-brain DeepSeek tokens when the framework did not report them.

### Component split (example: two-task UI run `evidence/phase-8/ui-v2/live-real/report.json`)

| Component | USD (approx.) |
|---|---:|
| cursor (Composer 2.5) | 0.074 |
| planner (flash) | 0.0005 |
| reviewer (flash) | 0.002 |
| jev | 0.0001 |
| worker brain | not reported |

### Wall-clock timelines (PR mode, two tasks)

| Milestone | Rehearsal fixture `59180665` | Rehearsal-3 `aa68e041` |
|---|---:|---:|
| First `pr.opened` | 300.6 s | 343.3 s |
| Both `pr.checks_green` | 326.9 s | 370.2 s |
| Both `pr.merged` | — | **746.0 s** |

Sources: `tests/fixtures/59180665-retry-events.jsonl` and `evidence/phase-12/rehearsal-3/timeline.json`. Gaps before the first PR are plan approval, fleet, pre-PR review, and open—not idle polling.

### Parallel speedup (why often &lt; 2× end-to-end)

| Run | Tasks | Wall (s) | Sequential task sum (s) | Reported speedup | Fleet speedup | Source |
|---|---:|---:|---:|---:|---:|---|
| `55d0986a` | 3 | 126.2 | 236.0 | 1.87 | 2.40 | `evidence/phase-4/live-4/report.json` |
| live-real | 2 | 257.7 | 434.9 | 1.69 | 1.79 | `evidence/phase-8/ui-v2/live-real/report.json` |

**Why fleet speedup can be ~2.4× but overall ~1.7–1.9×:** scan, baseline checks, planner, router, review, and integration run **sequentially**; tasks differ in Cursor duration; each worker pays MCP + card + first Cursor startup (often tens of seconds). See `docs/STATUS.md` (phase-4 live metrics).

### Revision round cost

| Scenario | Extra wall | Extra cost signal | Source |
|---|---:|---|---|
| E2#2 admin revise | +186 s review stage | +$0.07 vs E2#1 | `eval/report.md` variance |
| PR#84 comment revision | ~55 s comment→push; +42 s push→green | live session | `evidence/phase-11/live-pr-4/timings.txt` |

### What drives cost

- **Cursor tokens** dominate when tasks touch large files or need multiple `cursor_code` rounds (`[models.router]` tiers).
- **Planner/reviewer** scale with plan size and diff size (`models.strong` / `models.fast`).
- **Jev** is cheap per call but runs on guards and routing (`pricing.jev_usd_per_million_input`).

### How to reduce it

| Knob | Effect |
|---|---|
| Narrow `allowed_paths` per task | Smaller diffs, fewer Cursor tokens |
| `composer-2.5-fast` tier / `models.fast` planner | Lower $/token where quality allows |
| `[guards] max_tasks` (default 6) | Caps blast radius |
| `[revise] max_rounds` / reaction retries | Caps expensive revise loops |
| `[workers] max_workers` (default 3) | Bounds parallel spend |

---

## Quickstart

### Prerequisites

- Python **3.12+**
- `git`, `uv` (recommended)
- [`cursor-agent`](https://cursor.com) on `PATH` for live coding
- `gh` authenticated when using `[pr]`

### Init and doctor

Two directories matter:

1. **Clone** — the TreeFleet git checkout. Create the two virtualenvs here.
2. **Project dir** — where `treefleet.toml` lives (can be the clone, or a separate folder). `init` records absolute paths to the clone’s venvs when it finds them.

```bash
# In the TreeFleet clone:
uv venv .venv-orch && uv pip install -p .venv-orch -e ".[orchestrator,dev]"
uv venv .venv-worker && uv pip install -p .venv-worker -e ".[worker]"
# Unix: source .venv-orch/bin/activate
# Windows:
.\.venv-orch\Scripts\activate

# Project dir (clone or another folder). TREEFLEET_CONFIG can point here later.
treefleet init          # writes treefleet.toml + .env.example; asks for worktree root and target repo
treefleet init --yes --worktree-root /tmp/tf-wt   # non-interactive
# Windows: keep the worktree root short (default C:\wt). Paths must stay under 120 characters
# because each task lives at <root>/<run_id>/<task_id>.
```

`init --install` creates the venvs inside the detected TreeFleet checkout (the clone that provides the running `treefleet`) and prints those absolute paths. If no venvs exist yet, `init` omits the path keys and prints the create commands.

Set the names in `.env.example` as real environment variables (never commit values). From the project dir (or with `TREEFLEET_CONFIG` set):

```bash
treefleet doctor
treefleet doctor --repo <path-to-target-repo>
treefleet doctor --pr --live   # also GitHub + provider auth probes
```

Config path: `TREEFLEET_CONFIG` (absolute or relative path to a `treefleet.toml`) overrides the default `./treefleet.toml` for CLI, UI, doctor, and runs. `treefleet init --help` / `treefleet doctor --help` mention this.

### First look: `treefleet try`

No GitHub and no repo of your own. After init + doctor:

```bash
treefleet try --yes
# or: treefleet try --dir ~/treefleet-sample --goal "..."
```

Expected shape: doctor lines → confirmation (skipped with `--yes`) → a three-task run against a local sample under `~/treefleet-sample` → a results block with `run_id`, task branches `tf/<run>/<task>`, worktree root, report path, cost, and `git -C …` inspect/merge commands (shown, not executed). PR mode is forced off for this run. Your own repo comes after.

### Environment variables (names only)

| Variable | Required |
|---|---|
| `DEEPSEEK_API_KEY` | Yes |
| `TYPESAFE_API_KEY` | Optional (Jev). Without it, routing uses the built-in fallback tier |
| `CURSOR_API_KEY` | Optional but needed for live Cursor |
| `LANGFUSE_PUBLIC_KEY` | Optional |
| `LANGFUSE_SECRET_KEY` | Optional |
| `LANGFUSE_BASE_URL` | Optional |

**Jev (TypeSafe)** scores input risk and task complexity for the router. With `TYPESAFE_API_KEY` set, Jev complexity picks the Cursor tier (`source: jev`). Without it (or when Jev errors), the deterministic input guard still runs and the router maps each task's planner `complexity` to a tier (`trivial`/`simple`→simple, `moderate`→moderate, `complex`→complex; unknown→`default_tier`) with `source: heuristic`, emits `router.jev_skipped` when the key is missing plus `router.heuristic_used` when any task used the map, and `report.json` records `router: "fallback"` with `routing_sources` counts.

### Run on your own repo

Every CLI run prints a confirmation screen (repo, base branch, clean/dirty, PR mode, worktree root, agent CLI, budgets) unless `--yes` or `--no-approval`. Non-interactive sessions without `--yes` refuse with exit 2.

```bash
treefleet ui --repo <path-to-target-repo> --port 8765
# or
treefleet "<your goal>" --repo <path-to-target-repo> --yes
```

Open the board, submit a goal, approve the plan unless **Auto-run** is on. `--yes` accepts confirmation and detected checks without prompting.

### Enable the PR loop

`treefleet init` can write `[pr]` after confirming `gh` auth (or pass `--enable-pr --pr-repo owner/name`). Defaults live in [`src/treefleet/pr/config.py`](src/treefleet/pr/config.py). Manual shape:

```toml
[pr]
enabled = true
repo = "owner/name"
base = "main"
poll_seconds = 15
max_wait_minutes = 60
trusted_commenters = []   # empty → authenticated gh login
auto_merge = false
```

Then:

```bash
treefleet pr status <run_id>
treefleet pr watch <run_id>
treefleet pr retry <run_id> <pr_number>
treefleet pr merge <run_id>/<task_id>
```

### Sessions and cleanup

```bash
treefleet sessions ls
treefleet sessions show <run_id>
treefleet sessions kill --task <run_id>/<task_id>
treefleet sessions kill --run <run_id>
treefleet sessions cleanup <run_id> --yes [--force] [--include-unmerged] [--repo <path>]
```

`treefleet cleanup <run_id>` is an alias for `treefleet sessions cleanup` (same flags).

### Eval and reactions

```bash
treefleet eval --repo <path-to-demo-repo> [--scenarios eval/scenarios.toml] [--only E1,E2]
treefleet reactions list
treefleet reactions explain --run <run_id>
```

### UI replay

```bash
treefleet ui --run <run_id> [--port 8765]
treefleet ui --replay <path-to-events.jsonl> --repo <path-to-demo-repo>
```

CLI entry point after install: `treefleet` (`treefleet.cli:main`). `treefleet models` lists Cursor models and requires the same environment keys as a normal run.

---

## Configuration reference

Secrets **must not** appear in `treefleet.toml`. Unknown top-level keys are rejected.

### Core tables

| Key | Default | Meaning |
|---|---|---|
| `models.fast` | (see repo `treefleet.toml`) | Fast DeepSeek id (LiteLLM) |
| `models.strong` | (see repo `treefleet.toml`) | Strong DeepSeek id |
| `models.default_worker` | `router` | Default Cursor routing mode |
| `models.router.default_tier` | `moderate` | Tier when complexity is unknown (`source: router`) |
| `models.router.simple` / `moderate` / `complex` | Composer ids | Jev or heuristic complexity → Cursor model |
| `cursor.cli` | `cursor-agent` | Cursor executable name |
| `cursor.sandbox` | `disabled` on Windows | Passed to Cursor when supported |
| `thresholds.injection` / `abusive` / `on_topic` | 0.5 / 0.5 / 0.3 | Jev guard thresholds |
| `workers.port_start` | 9101 | First A2A port |
| `workers.mcp_startup_s` | 15 | MCP handshake timeout |
| `workers.max_workers` | 3 | Parallel task cap |
| `paths.worktree_windows` | short path | Windows worktree root (`<worktree-root>`) |
| `paths.worktree_other` | `/tmp/tf-wt` | Unix worktree root |
| `paths.orchestrator_venv` | `.venv-orch` | Orchestrator interpreter |
| `paths.worker_venv` | `.venv-worker` | Worker interpreter |
| `guards.max_tasks` | 6 | Plan guard task ceiling |
| `revise.max_rounds` | 1 | Reviewer re-dispatch cap (≤2) |
| `revise.round_budget_s` | 240 | Per revise wall budget |

### `[pr]` (optional; absent ⇒ disabled)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Turn on PR stage |
| `repo` | `""` | `owner/name` when enabled |
| `base` | _(required when enabled)_ | PR base branch; set explicitly or via `treefleet init --enable-pr` |
| `poll_seconds` | `15` | Poll interval (≥5) |
| `max_wait_minutes` | `60` | Stop waiting for PRs |
| `trusted_commenters` | `[]` | Logins allowed to drive revisions |
| `auto_merge` | `false` | Call `gh pr merge` when green |
| `allow_external_hooks` | `false` | Block unknown webhook hosts |

### `[reactions.*]`

Optional per-event overrides; see [`docs/REACTIONS.md`](docs/REACTIONS.md). `treefleet reactions list` prints the effective table.

---

## Repository layout

```
<repo>/
  src/treefleet/          # contracts, orchestrator, worker, git, guards, pr, ui
  tests/                  # phase-marked pytest (offline + @pytest.mark.live)
  scripts/gate.py         # cumulative quality gate
  docs/                   # architecture, phases, PR, reactions, sessions
  evidence/               # phase artifacts (screenshots, gate logs, live JSON)
  eval/                   # scenario harness + report.md
  runs/                   # per-run events.jsonl (gitignored; local only)
  treefleet.toml          # non-secret settings
```

---

## Testing strategy

| Layer | Command | What it proves |
|---|---|---|
| Fast gate | `python scripts/gate.py --phase N` | Ruff, pyright (orch + worker), pytest `phase0…N` |
| Live gate | `python scripts/gate.py --phase N --live` | Adds API-marked tests |
| Real spawn | Tests under `tests/test_phase3.py`, fleet tests | Real worker subprocess + fake LLM |
| UI | Playwright tests `test_ui_v2_*` | Board lanes, drawer, replay |
| Eval | `treefleet eval` | `eval/scenarios.toml` expectations |
| PR | `tests/test_pr_*` | Fake provider + recorded fixtures |

Never weaken a failing test to green the gate; fix the cause ([`docs/PHASES.md`](docs/PHASES.md)).

---

## Platform support

Developed and live-verified on **Windows only**. macOS and Linux code paths exist in the tree but are **not** live-verified. No cross-platform readiness claims beyond that.

## Limitations and known issues

- **Pre-1.0** — APIs, config keys, and board lanes still move with phases; read [`docs/STATUS.md`](docs/STATUS.md) before depending on behavior.
- **Windows Cursor** — No supported sandbox; isolation relies on proofs and scope revert, not kernel enforcement.
- **Worker brain usage** — Token counts from CrewAI are often missing; cost totals omit that component.
- **No resume** — A dead orchestrator cannot continue mid-task; inspect `run.json` and `events.jsonl`, then clean up.
- **Local-first** — Loopback UI; not a hardened multi-tenant service.
- **Phase 7 observability** — Langfuse wiring is deferred in the default build.

## Roadmap (planned, not implemented)

| Item | Intent |
|---|---|
| Plugin slots | Third-party tools at guarded boundaries |
| Tracker intake | Open work from GitHub issues into tasks |
| Live conflict-resolution run | End-to-end demo of merge conflict + resolve round |
| Chat notifier / fixer integration | Route human messages into the reaction table |
| Observability export | Langfuse or OpenTelemetry as a supported path |
| Cloud runtime | Hosted orchestrator with the same contracts |

## Inspired by

TreeFleet is **inspired by** [Agent Orchestrator](https://github.com/OrchestratorInc/agent-orchestrator) (OrchestratorInc). There is **no affiliation**, no parity guarantee, and no copied text or images from that project. TreeFleet is a separate Python codebase built on ADK and CrewAI.

---

## Documentation map

| Doc | Topic |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Components, isolation, tech choices |
| [`docs/CONTRACTS.md`](docs/CONTRACTS.md) | Event vocabulary and payloads |
| [`docs/PR_LIFECYCLE.md`](docs/PR_LIFECYCLE.md) | PR states, safety, comments |
| [`docs/REACTIONS.md`](docs/REACTIONS.md) | Event → action matrix |
| [`docs/SESSIONS.md`](docs/SESSIONS.md) | Lifecycle, cleanup, kill rules |
| [`docs/PHASES.md`](docs/PHASES.md) | Build phases and gate protocol |
| [`docs/STATUS.md`](docs/STATUS.md) | What is done vs deferred |
| [`AGENTS.md`](AGENTS.md) | Contributor hard rules |

---

## Contributing and security

- [CONTRIBUTING.md](CONTRIBUTING.md) — gates, conventions, scope.
- [SECURITY.md](SECURITY.md) — how to report issues and what we treat as in scope.

---

## License

MIT — see [LICENSE](LICENSE).
