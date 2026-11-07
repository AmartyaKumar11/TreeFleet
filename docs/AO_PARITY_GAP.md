# AO parity gap analysis

Research only. No AO code was copied into TreeFleet.

**Reference tree:** workspace `D:\agent-orchestrator` (remote `https://github.com/OrchestratorInc/agent-orchestrator.git`). Clone to `D:\ref\agent-orchestrator` was not required; the workspace copy was already present and tracked that origin.

**Sources read:** AO `README.md`, `docs/architecture.md`, `docs/STATUS.md`, session lifecycle / persistence notes, daemon wiring, lifecycle reactions, agent ports/registry, Cursor adapter, kanban/status derivation, frontend attention-zone presentation.

**License:** AO is **Apache-2.0** (`LICENSE` in the AO repo). If TreeFleet ever reused AO source (verbatim or modified): keep copyright and license notices, state significant changes, include the Apache-2.0 text with redistributed AO-derived files, and respect the patent grant/termination terms. Ideas and architecture described here are not a license to copy code. Prefer re-implementing against ADK/CrewAI contracts. TreeFleet itself is not forced open-source merely by reading AO.

---

## 1. Session lifecycle and states (staying alive between agent calls)

### What AO does
AO is a **long-running Go daemon**. A worker is a durable **session**, not a one-shot job.

Durable facts (not display labels) live in SQLite: `activity_state` (`active` | `idle` | `waiting_input` | `blocked` | `exited`), `hibernated_at`, `is_terminated`, mode/runtime handles, PR tables. Display statuses like `working` / `needs_input` are **derived at read time**.

Cite:

- `docs/architecture.md` ~L3–19 (daemon + TUI/Chat controllers), ~L51–80 (durable facts, Chat hibernation), ~L330–352 (spawn flow), ~L788+ (lifecycle manager)
- `backend/pkg/contract/status.go` L5–41 (activity + display enums), L106–137 (`DeriveStatus`)
- `docs/STATUS.md` L35–61 (session HTTP lifecycle; detached Chat hosts survive daemon/desktop replacement)
- Cursor resume: `backend/internal/adapters/agent/cursor/cursor.go` L114–137 (`GetRestoreCommand` → `cursor-agent … --resume <id>`)

Between turns the agent process (or detached Chat host) **stays up**; restore uses the native session id. Hibernation stops a settled Chat host and wakes it on new work without treating hibernation as a board status.

### What TreeFleet does today
A run is a **finite ADK `SequentialAgent` pipeline** (`orchestrator/pipeline.py`) over an **in-memory** ADK session. When the pipeline ends, the run is done.

Each `cursor_code` call **cold-starts** a headless `cursor-agent` subprocess (`cursor_run.py`), waits for JSON, then kills it. Revise rounds re-dispatch work, but still pay a new Cursor process per call. Worker A2A processes are killed after the task wave (`fleet.py` finally). There is no SQLite session row, no `waiting_input`, no hibernate/resume of Cursor across calls.

### Smallest ADK/CrewAI change
Keep the **same CrewAI worker process and worktree** across revise rounds (already mostly true for revise on the same branch), and extend `cursor_code` to:

1. Capture Cursor’s native session id from the CLI result/hooks when present.
2. On the next call in that worktree, launch with **`--resume <id>`** instead of a full cold start when the id exists.

No new agentic framework. Still ADK orchestrator + CrewAI worker + Cursor as the coding tool. Optional later: do not tear down the worker A2A server until the whole run finishes review for that task.

---

## 2. Routing failed checks, review comments, and merge conflicts back to the owning worker

### What AO does
The **SCM observer** writes PR/CI/review facts. The **lifecycle manager** turns those into **nudges** pasted into the **same session** that owns the PR (via `AgentMessenger` / session guard), with dedup signatures so the same failure is not spammed.

Cite:

- `backend/internal/lifecycle/manager.go` L216–217 (owns CI / review / merge-conflict nudges)
- `backend/internal/lifecycle/reactions.go` L166–277 (`AutoInjectCI`, unresolved review comments with `AutoInjectReview`, `ReviewChangesRequest`, urgent merge-conflict rebase message)
- `docs/architecture.md` observation / lifecycle sections; README “Close the feedback loop” (~L94)

Gates matter: CI/review nudges wait while the agent is at ambiguous `waiting_input`; merge-conflict can be **urgent** for harnesses that surface permissions as `blocked`.

### What TreeFleet does today
**In-process review only.** `ReviewerAgent` / `review_loop` (`orchestrator/review.py`) returns `approve` | `revise` | `reject`. A `revise` re-runs the **same worktree/branch** with `revision_feedback` in the envelope (cap from `treefleet.toml` `[revise] max_rounds`). Failed **local** checks now revise with a redacted check tail **before** the reviewer. Integration conflicts merge the integrated tip into the owning worktree and give that worker **one** Cursor resolve attempt, then exclude on a second conflict.

There is **no** GitHub/GitLab observer and **no** auto-inject of remote review comments or CI into the worker.

### Smallest ADK/CrewAI change
Stay hub-and-spoke on the orchestrator:

1. Treat failing `RunRequest.checks` and integrator `conflict` as first-class `revision_feedback` (or a new `WorkerTaskEnvelope` reason) and re-enter the existing revise path on the **owning** task id.
2. Optional demo-sized addition: if a PR URL is known, one `gh`/`glab` poll from **orchestrator plain code** (not a new framework) that formats failing checks / review bodies into the same `revision_feedback` string CrewAI already understands.

Do not give workers peer messaging; orchestrator remains the only router.

---

## 3. Board lane rules (Working, Needs you, In review, Ready to merge)

### What AO does
Two related layers:

1. **Kanban column** from PR/session facts: `building` | `validating` | `needs_review` | `ready` | `archive` — `backend/pkg/contract/kanban.go` L11–28, L96–122 (`derivePRKanbanColumn`), L242–261 (`DeriveKanbanPresentation`).
2. **Attention zones** shown as the four human lanes — `packages/product-ui/src/session-presentation.ts` (`attentionZone`, labels `Working` / `Needs you` / `In review` / `Ready to merge`); mapping covered in `frontend/src/renderer/lib/session-presentation.test.ts` L91–109:
   - Working ← `working`, `idle`
   - Needs you ← `needs_input`, `exited`, `no_signal`, `ci_failed`, `changes_requested`, …
   - In review ← `review_pending`, `pr_open`, `draft`
   - Ready to merge ← `approved`, `mergeable`, `merged`

README L77–82 states the product meaning of those four lanes.

### What TreeFleet does today
The lite board uses the **same four lane titles**. Lanes are derived in Python (`ui/lanes.py`) from events and report fields and shipped over SSE as `task.lane`; the page does not guess. `/api/board` also carries `needs_human` for refresh, with a browser Notification and badge when attention is required.

### Smallest ADK/CrewAI change
Keep the four lanes. Replace `lane()` with a pure function over **existing events + report fields** (still no Electron):

| Fact | Lane |
|---|---|
| `awaiting_approval`, `needs_review`, failed checks, `conflict`, blocked guard | Needs you |
| worker active / `output_line` / fleet in progress | Working |
| `review_started` / awaiting verdict / done pending integrate | In review |
| `verdict=approve` and integrated (or ready to merge) | Ready to merge |

Implement as one shared Python helper used by SSE payloads so the board does not invent status. Still ADK/CrewAI underneath.

---

## 4. Agent plugin interface (adding a new coding agent)

### What AO does
Every coding CLI is an **adapter** implementing `ports.Agent` (launch/restore/hooks/session info). Optional capability interfaces extend auth, models, handoff, etc.

Cite:

- `backend/internal/ports/agent.go` L165–191 (`Agent` interface)
- `backend/internal/adapters/agent/registry/registry.go` L1–5, L52–110 — **one edit to `Constructors()`** registers a harness; daemon wires sessions through the registry

Cursor is one plugin among many (`cursor.New()` in that list).

### What TreeFleet does today
The coding agent is **hard-wired**: CrewAI’s only write path is `cursor_code` → `cursor_run.run_cursor`. There is no plugin registry. Swapping Codex/Claude CLI would mean forking the tool module. Framework rule still holds: Cursor is an external coding tool, not an agentic framework.

### Smallest ADK/CrewAI change
Introduce a tiny **CodingDriver protocol** in shared (non-ADK, non-CrewAI) code, e.g. `run(instruction, worktree, model) -> CursorRunResult`-shaped result, with:

- `CursorDriver` (current behavior)
- later `ClaudeCodeDriver` / `CodexDriver` as alternate subprocess wrappers

CrewAI `build_agent` takes the driver from config (`treefleet.toml` / env). ADK fleet envelope gains `coding_driver` id. No LangChain/etc. Registration = one dict in config + one module, analogous to AO’s `Constructors()` but Python-side and tool-shaped.

---

## 5. Notifying the user when an agent needs input

### What AO does
`activity_state = waiting_input` / `blocked` → derived `needs_input` → **Needs you** zone. Desktop tray groups attention sessions; UI settings include **sound** for attention-worthy notifications (`frontend` tray + `soundNotificationsEnabled`). Chat approvals and permission dialogs keep the session alive until the human acts. Automation must not inject into `blocked` sessions (`docs/architecture.md` L56).

### What TreeFleet does today
Human gates: **plan approval** (CLI prompt / UI Approve) and board **Needs you** cards for revise/fail/`needs_review`. Live pane is read-only; Feedback box is not sent. **No** OS tray, **no** desktop notification, **no** sound, **no** `waiting_input` fact while Cursor or the worker waits on the human mid-tool.

### Smallest ADK/CrewAI change
When emitting `awaiting_approval` or placing a task in Needs you:

1. SSE event already reaches the board — add `Notification` Web API (localhost page) + optional beep.
2. Persist a boolean `needs_human` on the run status endpoint so refresh still shows the badge.

No daemon rewrite. Still FastAPI + ADK pipeline.

---

## 6. What makes AO faster than TreeFleet’s ~20s per-call Cursor startup

### What AO does that avoids cold start
1. **Long-lived agent process** (TUI PTY or Chat controller) — turns are messages into an existing process, not a new OS spawn each time.
2. **`cursor-agent --resume <id>`** after the first launch (`cursor.go` L114–137).
3. **Detached persistent Chat hosts** for several providers — desktop/daemon replacement does not kill the in-flight provider (`docs/STATUS.md` L46–57; Codex persistent host wiring under `chatdriver`).
4. **Hibernate/wake** for settled Chat — cold when idle, warm resume on send (`docs/architecture.md` L67–80).
5. Background title/slug work is **non-blocking** on spawn (architecture spawn notes ~L356–360).

### What TreeFleet does today
Every coding step: spawn `cursor-agent --print …`, wait, kill (`cursor_run.py`). STATUS notes measured Cursor steps tens of seconds; MCP startup alone defaults to 15s budget. Revise rounds multiply that cost.

### Smallest ADK/CrewAI change (highest leverage)
**Resume-or-warm Cursor inside the worker tool** (same as §1):

- First call: full launch; store session id on disk under the worktree (or in worker memory for the process lifetime).
- Later calls / revise: `--resume` + new instruction only.
- Keep the CrewAI worker process up for the task lifetime so MCP handshake is not repeated every revise.

Second lever (still in-policy): prefer **stream-json** on a long-lived Cursor child for multi-step edits inside one `cursor_code` invocation (spike S12 already sampled stream events) so one startup serves many tool turns.

Do **not** copy AO’s Go host; reimplement warm resume in `cursor_run.py` + crew tool.

---

## Ranked parity gaps

### By effort (low → high)

| Rank | Gap | Effort | Notes |
|---|---|---|---|
| 1 | Board lane derivation from existing events | S | Pure function + SSE field |
| 2 | Browser/OS-lite notify on Needs you / approve | S | Notification API + event |
| 3 | Route check failures & merge conflicts into revise feedback | S–M | Extend `review_loop` / integrator messages |
| 4 | Cursor `--resume` / warm worker across calls | M | Biggest runtime win; touches `cursor_run` + envelope |
| 5 | CodingDriver plugin registry | M | Config + protocol; second driver optional |
| 6 | Full AO-style session daemon + SCM observer + tray | L | Out of scope for ADK/CrewAI-only course shape |

### By demo value (high → low)

| Rank | Gap | Demo value |
|---|---|---|
| 1 | Cursor warm resume (cut multi-call latency) | High — teacher feels speed |
| 2 | Needs-you notify + clearer lane rules | High — board reads like AO |
| 3 | Failed checks / conflicts back to owning worker | High — “feedback loop” story |
| 4 | CodingDriver (show Claude/Codex swap) | Medium — only if second CLI is installed |
| 5 | Full SCM auto-inject from GitHub | Medium–High but heavy |
| 6 | Detached host / hibernate parity with AO daemon | Low for viva unless cloud story returns |

### Build first (recommended three)

1. **Cursor warm resume + keep worker process for the task** — closes the 20s/call gap with the smallest durable change.  
2. **Board lane rules + human notify** — makes the UI explain itself the way AO’s Kanban does, using facts TreeFleet already emits.  
3. **Route local check failures and integrate conflicts back as `revision_feedback`** — completes the “owning worker” story without copying AO’s SCM stack.

Defer full Apache-licensed AO daemon/SCM/tray parity; reimplement only the behaviors above on ADK + CrewAI + plain Python.
