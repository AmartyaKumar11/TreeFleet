# 15.D5.0 — Orchestrator transcript data audit (read-only)

**Status:** audit only. No product code changes. Spec target: `docs/ui-design-spec.md` §8.11 (Verbose / Normal / Summary, 720 px column, serif messages, plan approval card).

**Evidence run:** `runs/aa68e041` against `D:\pr-sentinel-demo` (`AmartyaKumar11/pr-sentinel-demo`). Goal: unit tests for `src/notifications.py` and `src/admin.py`. Log: 566 lines, 114 929 bytes. Redacted samples: `spikes/ui-transcript-audit/` (regenerate with `spikes/ui-transcript-audit/extract_samples.py`).

---

## Verdict

The board can already show a thin orchestrator story (goal, guard pass, plan card with titles/paths, worker rows, verdicts, PR lines, integration summary). It cannot show a Claude/Codex-style transcript yet: tool rows lack input/output/duration, planner/reviewer **text** is not in the event log, `seq` is not a safe cursor across processes, and `/api/board` keeps only the last 400 events while dropping plan instructions/rationale.

---

## 1. Event types in the run log

### Envelope

Every line is a `RunEvent` (`contracts.py:916-926`): `run_id`, `seq`, `ts`, `source`, `type`, `task_id`, `payload`. Vocabulary: `docs/CONTRACTS.md` §5. Sink: `events.py:EventSink._record` (`191-215`) assigns `seq`, runs `redact_payload`, appends JSONL under `events.jsonl.lock`.

### Ordering and `seq` (critical)

| Claim in CONTRACTS.md | Reality on `aa68e041` |
|---|---|
| "`seq` is a per-run monotonically increasing integer" | **False across processes.** Orchestrator sink is monotonic for its own emits. Workers call `_sink()` → **new `EventSink` every time with no `prime()`** (`worker/crew.py:753-755`), so many worker lines land as `seq: 0`. `worker.ready` bypasses the sink and hardcodes `seq=0` (`worker/main.py:16-28`). |
| SSE resumes with `Last-Event-ID` = `seq` | **Unsafe.** SSE skips `seq <= cursor` (`ui/server.py:605-612`). After the client has seen orchestrator `seq=18`, later worker lines with `seq=0` are **never streamed**. |

Measured on `aa68e041`: `seq` min 0, max 215, **59 lines with `seq=0`**, duplicates common, file order is not seq order. File append order under the lock is chronological; use **byte offset or line index**, not `seq`, until emitters are fixed.

`EventSink.prime` (`events.py:140-157`) exists for a second process that keeps one sink alive; workers do not use it today.

### Event type × fields × emitter (observed + contracted)

Typical size = mean JSONL line length on `aa68e041` (~201 B avg, 1376 B max) unless noted.

| type | emitter (file) | payload fields (observed / contract) | ~size | notes |
|---|---|---|---|---|
| `guard.decision` | `pipeline.py`, `agents.py`, `worker/callbacks.py` | `stage`, `allowed`, `detector`, `reasons`, `scores`, `task_id`, `ts` | ~250 B | First sample seq 0 (orchestrator sink start) |
| `orchestrator.agent_start` / `_end` | ADK callbacks / agents | `name`, `duration_s` | ~120 B | |
| `phase` | `agents.py` `_phase` | `name` (`scan`/`planner`/`router`/`fleet`/…), `duration_s` | ~110 B | |
| `orchestrator.model_call` | `orchestrator/callbacks.py:51-77` | `role`, `phase` before/after, `chars` or tokens + `duration_s` | ~150 B | **No model text** |
| `orchestrator.tool_call` / `tool_result` | `callbacks.py:83-100` | `name`, `phase`, `duration_s` on after | — | Not in this run; metadata only |
| `awaiting_approval` | `agents.py:940-947` | `{approved: bool}` only | ~100 B | **No plan body** |
| `approval.granted` | `agents.py:950-957` | `source` (`ui`/`http`), `approved` | ~110 B | |
| `model_selected` | `agents.py` | `task_id`, `model`, `source`, `complexity` | ~160 B | |
| `worker.ready` | `worker/main.py:16-28` via `append_lines` | `pid`, `cwd`, `task_id` | ~180 B | **seq always 0; bypasses sink** |
| `worker.spawned` | `fleet.py` | `upstream`, `pid` | ~120 B | |
| `worker.step` | `timing.py:53-62` | `StepTiming`: `step`, `duration_s`, `timed_out`, `name`, `output_tail`≤2000 | ~150–400 B | Often `seq=0` |
| `worker.step_callback` / `task_callback` | `worker/callbacks.py` | `name`/`duration_s` | ~120 B | |
| `tool.arg_error` | `worker/callbacks.py` | `tool`, `message`, `arguments` | ~140 B | |
| `tool.error` | `worker/callbacks.py` | `tool`, `message` | — | |
| `output_line` | `cursor_run.py:225-237`, `callbacks.emit_output_line` | `line` ≤200 chars, redacted | ~100 B | Assistant stream deltas only |
| `cursor.tool_start` / `tool_finish` | `cursor_run.py:240-251` | `tool`≤40, `path`≤300, `ok` on finish | ~160 B | **No args/body/duration** |
| `worker.exited` | `fleet.py` | `status`, `files[{path,additions,deletions}]`, `error`, `a2a` | ~200–500 B | |
| `review_started` | `review.py` | `round` | ~100 B | |
| `review_verdict` | `review.py` | `round`, `verdict`, `reason`, `suggestions[]` (sometimes `cursor_message`) | up to ~1.3 KB | Largest lines in sample |
| `reaction.fired` | `pipeline.py` | `event`, `task`, `action`, `attempt`, `max_attempts`, `escalate`, `round` | ~180 B | |
| `pr.*` | PR lifecycle | number, url, sha, checks, author, reasons — **not comment bodies** | varies | See CONTRACTS §5 |
| `guard_redacted` | `events.py:_record` | `count`, `stage` | ~100 B | Auto-emitted when redact hits |
| `guard_degraded`, `router.*`, `isolation.proof_detail`, `generated_artifact_reverted`, `worker.killed`, `mcp.call`, `protocol_error`, `git.*`, `scope_violation`, `jev.call` | various | see CONTRACTS §5 | — | Not all present in `aa68e041` |

### Sample (≤20 lines, redacted) — file head of `aa68e041`

```text
0 guard          guard.decision
1 orchestrator   orchestrator.agent_start  repo_scan
2 orchestrator   phase                     scan
3 orchestrator   orchestrator.agent_end
5 orchestrator   orchestrator.model_call   planner before chars=2314
8 orchestrator   awaiting_approval         {approved:true}
9 orchestrator   approval.granted          {source:ui}
13 orchestrator  model_selected            composer-2.5
0 worker         worker.ready              seq hardcoded 0
18 orchestrator  worker.spawned
0 worker         worker.step               start
15 worker        cursor.tool_start         tool=read path=tests/…
16 worker        cursor.tool_finish        ok=false
20 orchestrator  worker.exited             status=done files=[…]
32 orchestrator  review_verdict            approve + suggestions
41 orchestrator  pr.opened                 #92 AmartyaKumar11/pr-sentinel-demo
```

Full one-of-each JSONL: `spikes/ui-transcript-audit/event-types-one-each.jsonl`.

---

## 2. What the board receives today

### `GET /api/board?run=<id>` — `Board.snapshot` (`ui/server.py:263-318`)

| Field | Used by orchestrator view? | Notes |
|---|---|---|
| `goal`, `worker_model`, `stage`, `stages`, `run_id` | yes | |
| `awaiting` | yes (plan card) | **While waiting only:** `[{id, title, allowed_paths}]` — **instructions, rationale, depends_on, checks stripped** (`269-272`) |
| `events` | partially | **`events[-400:]` only** (`303`). On this run drops first 166 lines including guard, plan approval, early tools |
| `tasks` | yes | Derived by `lanes.tasks_from_snapshot` |
| `report` | yes (integration summary) | Full `report.json` when present |
| `needs_human`, `message`, `started_at`, `finished_at` | yes | |
| `worktrees` | kanban/drawer | |
| `runs` | run history | goal truncated to 140 chars (`360-361`) |
| `repo`, `branch`, `head`, `clean` | header | |
| `pr_mode`, `pr_repo`, `status_line` | conditional | |

Whole body passed through `redact_payload` again (`317`).

### Dropped between event log / `run.json` and API

- Plan **instructions** and **rationale** (live in `run.json` / ADK session; not in `awaiting`).
- Events before the last 400.
- Full tool args/results (never stored — see §3).
- Planner/reviewer raw model messages (never stored as text events).
- PR comment bodies (CONTRACTS: deliberately omitted).

### `GET /api/worktrees`

`{worktrees: [...]}` after `redact_payload` (`446-456`). Paths, branch names, dirty flags. Orchestrator view uses worktrees mainly via board snapshot, not this route alone.

### `GET /api/runs/{id}/events` (SSE)

Streams redacted event dicts with `id: {seq}` (`582-619`). Re-reads **entire** `events.jsonl` every 0.4 s. Broken for `seq=0` worker traffic once cursor advances (see §1). Board also opens this fetch (`board.js:990`) but the 1 s poll of `/api/board` is the main snapshot path (`1769`).

### Other endpoints the UI uses

| Endpoint | Role |
|---|---|
| `POST /api/runs` | start | body `{goal, repo?, worker_model?, plan_approval?}` |
| `POST /api/runs/{id}/approve` | plan approve | no body; optional `x-treefleet-client: ui` (`565-572`) |
| `POST /api/runs/{id}/cancel` | plan reject | no body (`574-580`) |
| `GET /api/diff` | drawer diffs | |
| `GET /api/models` | model dropdown | |

**No plan-edit endpoint.** Spec §8.11 “edit” is greenfield.

### Sample board-shaped awaiting strip

```json
[
  {
    "id": "add-notifications-unit-tests",
    "title": "Add unit tests for notifications module",
    "allowed_paths": ["tests/test_notifications.py"]
  }
]
```

Full plan with instructions lives in `runs/aa68e041/run.json` under `plan` (not returned by `/api/board` awaiting).

---

## 3. Tool calls and cursor stream-json

### Persisted as events

| Need | Present? |
|---|---|
| Tool name | yes (`CursorToolEvent.tool`) |
| Tool input (args / content) | **no** — path only |
| Tool output / file body | **no** |
| Duration | **no** on cursor tools (pair start/finish timestamps yourself from `ts`) |
| Exit / ok | `ok` on `cursor.tool_finish` only (`success` key in stream result → bool) |

CrewAI FS tool failures: `tool.arg_error` / `tool.error` with message (and args on arg_error). Orchestrator ADK tools: name + duration only.

### `parse_stream_line` (`cursor_run.py:153-203`)

| stream-json `type` | Fate |
|---|---|
| `assistant` with `timestamp_ms` and **no** `model_call_id` | → `output_line` after `redact_line` (≤200 chars, single line) |
| `assistant` with `model_call_id` or without `timestamp_ms` | **discarded** (rollup / final repeat) |
| `tool_call` subtype `started` | → `cursor.tool_start` (tool + path) |
| `tool_call` completed | → `cursor.tool_finish` (tool + path + ok); **result body discarded** |
| `thinking` | **discarded** |
| `system` / `user` | **discarded** |
| `result` | usage extracted into `CursorRunResult`; result text truncated for usage path, not as `output_line` stream |

Reference sample of raw stream (not persisted verbatim): `docs/spikes/s12-cursor-stream.sample.txt`.

### Sample persisted tool rows

```json
{"seq":15,"type":"cursor.tool_start","payload":{"tool":"read","path":"tests/test_notifications.py","ok":null}}
{"seq":16,"type":"cursor.tool_finish","payload":{"tool":"read","path":"tests/test_notifications.py","ok":false}}
{"type":"output_line","payload":{"line":"Creating"}}
```

---

## 4. Orchestrator-level messages

| Artifact | Stored? | Where | Streaming? |
|---|---|---|---|
| User goal | yes | `Board.goals`, `report.json`, catalog | final only |
| Planner model text | **no** in events | Only `orchestrator.model_call` char/token counts | final metrics only |
| Plan JSON | yes | ADK session → `run.json` `plan`; in-memory `ApprovalGate.plan` while waiting | final plan object |
| Approval request | event shell only | `awaiting_approval` `{approved}` — plan shown via live `awaiting` strip or CLI print | final |
| Approval / reject | yes | `approval.granted` or cancel path (no dedicated `approval.denied` event; cancel sets gate `ok=False`) | final |
| Reviewer verdict text | partial | `review_verdict.reason` + `suggestions` | final |
| Integrate summary | yes | `report.integration` | final |
| Cursor assistant prose | partial | many short `output_line`s | **partial deltas** (200-char lines), not a single assistant message |

### Approval HTTP API (existing)

| Action | Method | Path | Payload |
|---|---|---|---|
| Approve | `POST` | `/api/runs/{run_id}/approve` | empty; header `x-treefleet-client: ui` marks source `ui` |
| Reject / cancel | `POST` | `/api/runs/{run_id}/cancel` | empty |
| Edit plan | — | **does not exist** | — |

---

## 5. Secrets and untrusted text

### Redaction before API / disk

1. **Emit path:** `EventSink._record` → `redact_payload` (`guards/text.py:28-48`) on every payload; patterns `sk-…`, `apikey_…`, `Bearer …`, PEM blocks. Hits also emit `guard_redacted`.
2. **Cursor lines:** extra `redact_line` (`cursor_run.py:60-62`) — same secret regex family, strip CR/LF, cap 200.
3. **Board snapshot / SSE / worktrees:** `redact_payload` again on the JSON body.

### Can tool output contain secrets?

Raw cursor stream **can** (file contents, env echoes). Persisted tool events **do not** store that body — only path. `output_line` and `worker.step.output_tail` **can** still carry secret-shaped substrings; they go through redact. Unmatched secret formats (e.g. some cloud keys) would leak into the log and then the API.

### Attacker-influenced fields (treat as text only)

| Field | Source |
|---|---|
| Goal / issue text | user / PR |
| Task titles, instructions | planner model (untrusted) |
| Branch names | derived `tf/<run>/<task>` plus git |
| File paths / contents | repo + agent |
| `output_line`, tool messages, review suggestions | models / CLI |
| PR authors, check labels | GitHub |
| Guard `reasons` | detectors + model text |

UI must keep using escape / `safe-text` — never `eval` or interpret as instructions.

---

## 6. Size, retention, polling, locks

| Topic | Finding |
|---|---|
| Growth | `aa68e041`: **~115 KB / 566 lines** (~200 B/line). Dominated by `output_line` (371). A chatty Cursor run scales with assistant deltas + 2× tool events. |
| Rotation | **None.** One `events.jsonl` per run until manual cleanup. |
| Max line | Soft caps via models (`OutputLine` 200, `output_tail` 2000, review reasons truncated). No hard JSONL line limit in `append_lines`. |
| 1 s poll cost | `snapshot` → `read_events` reads **entire file** every poll (`events.py:64-67`, `server.py:266`). At 115 KB trivial; at 5 000 events (~1 MB) still fine on SSD; at tens of MB becomes wasteful. SSE also full-file rescans every 0.4 s. |
| Byte-offset tailing | **Not implemented.** Would be safe for readers if writers only O_APPEND under the lock (current write path). Readers must tolerate torn last line. |
| Lock | `append_lines` (`99-129`): lock file + `msvcrt.locking` / `fcntl.flock`. Windows lock acquire **retries forever** on `OSError` with 1 ms sleep (`70-80`). **`os.open` / `os.write` PermissionError is not caught — it propagates** and fails the emit. Contrast: `sessions.load_run` / replace retries PermissionError 5× (`sessions.py:95-115`) for `run.json` only — **not** the events lock. |

---

## 7. Proposed minimum backend contract

### `GET /api/transcript?run=<id>&after=<n>&limit=<n>`

Suggested query:

- `run` (required): run id  
- `after` (default `-1`): return items with **`index > after`** where `index` is **0-based file order** (not `seq`, until seq is fixed)  
- `limit` (default `200`, max `500`)

Response schema (draft):

```json
{
  "schema_id": "treefleet.transcript.v1",
  "run_id": "aa68e041",
  "after": 100,
  "next": 300,
  "eof": false,
  "items": [
    {
      "index": 101,
      "seq": 42,
      "ts": "2026-11-10T11:53:01Z",
      "source": "worker",
      "type": "cursor.tool_start",
      "task_id": "add-notifications-unit-tests",
      "payload": { "tool": "read", "path": "tests/test_notifications.py", "ok": null }
    }
  ],
  "plan": {
    "goal": "…",
    "rationale": "…",
    "tasks": [{ "id": "…", "title": "…", "allowed_paths": ["…"], "instructions": "…" }]
  },
  "awaiting": false
}
```

Caps / redaction:

- Always `redact_payload` on items + plan strings.  
- Omit or hard-cap any future `input`/`output` fields (recommend **truncate to 2–4 KB** or store by reference under `runs/<id>/blobs/`).  
- Do not return raw stream-json.

### Verbose / Normal / Summary (derive vs emit)

| Mode (spec §8.11) | Shows | Data source today | Smallest change |
|---|---|---|---|
| **Verbose** | Every tool row one line; expand chevron if input/output exist | name+path+ok only → **no chevron content** | Optional: add `duration_ms` by differencing finish-start `ts` in the API (derive). Storing args/output is a **product decision** (see Decisions). |
| **Normal** | Group consecutive tools (“Edited 3 files…”) | derive from `cursor.tool_*` + paths | **derive only** |
| **Summary** | Messages, plan, verdicts, integration | goal + `awaiting`/`plan` + `review_verdict` + `report.integration` + sparse `output_line` | Return `plan` from `run.json` / gate on transcript endpoint (**add read**, no new emit). |

### Emitter changes vs derive

| Item | Action |
|---|---|
| Stable cursor for `after=` | **Must add:** single shared sequence — either always `prime()` one worker sink per process, or write `index`/byte offset; stop hardcoded `seq=0` in `worker.ready` |
| Plan in transcript | **Derive** from `run.json` / approval gate; optionally emit `plan.ready` with stripped tasks once |
| Tool duration | **Derive** from paired `ts` |
| Tool input/output | **New fields** only if Decision says store; else UI hides chevron |
| Assistant message bubbles | **Derive** by joining `output_line` per task with gaps; or emit `assistant.message` finals (new) |
| Approve / reject / edit | Approve+cancel exist; **edit = new endpoint** if required by §8.11 |

---

## 8. `orchestrator.js` trust and scale; sinks without `safe-text`

### Trust / scale issues in `orchestrator.js`

- Rebuilds the whole thread with `root.innerHTML = html` every poll (`280`). Uses `esc()` for interpolations — **XSS-escaped, but not `safe-text` BIDI / invisible-char handling**.
- Renders attacker-influenced strings: `goal`, task `title`, `branch`, `reason`, PR `author`, integration `excluded` reasons (`104-275`).
- `dataset.rendered = html` stores full HTML string; at **5 000 events** the board already truncates to 400, but if transcript feeds this pattern it will **O(n) re-stringify and reparse DOM every 1 s** — will jank. Need incremental append + virtualize.
- Plan approve/cancel buttons present; no edit.
- Does not consume tool rows or `output_line` today — transcript is a new surface.

### Board text sinks that do **not** pass through `safe-text.js`

`safe-text` is used in header/drawer/diff paths (`board.js` imports `setSafeText` for project path/title; `drawer.js`, `diff-render.js`). These sinks use `textContent`, `esc`+`innerHTML`, or raw `.title` instead:

| Sink | File | Mechanism |
|---|---|---|
| Kanban card title, branch, model, reasons, notices | `board.js` `fillCard` ~760-788 | `esc` + `innerHTML` |
| Plan card steps | `board.js` ~891-913 | `esc` + `innerHTML` |
| Run history goals / ids | `board.js` `renderRunHistory` ~629-633 | `esc` + `innerHTML` |
| Lane empty states, stage/status lines, elapsed | `board.js` | `textContent` / `innerHTML` |
| Guard / proof / cost / worktree tables | `board.js` ~1293-1360 | `esc` + `innerHTML` |
| Git graph branch labels | `git-graph.js` ~110-111 | `textContent` + `.title` |
| Orchestrator thread | `orchestrator.js` | `esc` + `innerHTML` |
| Notifications / toasts (if any in board) | `board.js` title badge ~177-198 | `textContent` / `document.title` |
| Command palette options | `palette.js` | `textContent` |
| Diff chrome button titles | `diff-render.js` | fixed strings / path via safe append for content |

`esc` prevents HTML injection; it does **not** neutralize bidi overrides or invisible chars the way `sanitizeBidi` / `setSafeText` do. Spec for D5 should route transcript and card titles through `safe-text`.

---

## Risks

1. **`seq` as resume cursor is wrong today** — SSE and any `after=seq` API will drop worker traffic.  
2. **Last-400 window** hides the plan-approval story on long runs.  
3. **Storing full tool output** reintroduces secrets and huge logs; path-only is current safety posture.  
4. **Full-file read every poll / SSE tick** will not scale without tailing.  
5. **Windows PermissionError on event write** fails the emit (no retry); lock wait retries only.  
6. **innerHTML orchestrator thread** at transcript scale will thrash the main thread.  
7. **Untrusted model/PR text** in suggestions and titles — display-only discipline required.

---

## Decisions you must make before D5 implementation

1. **Tool output storage:** full / truncated (e.g. 2 KB) / by reference blob / **keep path-only** (status quo)? Spec chevrons need more than path-only.  
2. **Resume cursor:** fix multi-process `seq` + `prime()`, or introduce monotonic `index`/byte offset and teach SSE to use it?  
3. **Plan on the wire:** enrich `awaiting` with instructions, or only expose full plan on `/api/transcript` / `run.json`?  
4. **Plan edit:** ship Reject+Approve only for D5, or add `POST …/plan` with edited JSON?  
5. **Assistant messages:** join `output_line` deltas vs new final `assistant.message` events?  
6. **Retention:** keep unbounded JSONL, or rotate / cap lines per run?  
7. **Transcript transport:** poll `GET /api/transcript` on 1 s, fix SSE, or both?

---

## Report checklist

| Deliverable | Path |
|---|---|
| This audit | `docs/ui-transcript-data-audit.md` |
| Throwaway extractor | `spikes/ui-transcript-audit/extract_samples.py` |
| Redacted samples + stats | `spikes/ui-transcript-audit/*.jsonl`, `stats.json`, `board-api-shape.json` |
| Event × fields | §1 table |
| Proposed schema | §7 |
| Risks / decisions | above |

### Untracked / unexpected files created by this audit

- `docs/ui-transcript-data-audit.md`
- `spikes/ui-transcript-audit/extract_samples.py`
- `spikes/ui-transcript-audit/event-types-one-each.jsonl`
- `spikes/ui-transcript-audit/events-head-20.jsonl`
- `spikes/ui-transcript-audit/cursor-and-output-20.jsonl`
- `spikes/ui-transcript-audit/stats.json`
- `spikes/ui-transcript-audit/board-api-shape.json`

No product code under `src/` was modified. No demo reset, no git mutations, PRs #80/#77 untouched.

---

## Implemented (15.D5a)

Decisions locked and shipped in product code (no UI):

1. **Cursor = byte offset** of the next unread complete line. `seq` stays informational per emitter; emitters were not changed for seq. `docs/CONTRACTS.md` §5 updated.
2. **`CursorToolEvent.detail`** on `cursor.tool_start` (≤300, `redact_line`, allowlisted args). Tool results still discarded.
3. **`approval.denied`** on cancel while awaiting (`source` ui/http).
4. **`GET /api/transcript`** with optional `plan=1` full plan; `/api/board` `awaiting` unchanged.
5. Items are raw events (no server-side grouping).
6. No rotation, no plan edit.

Also: `append_lines` retries transient `PermissionError`/`OSError` (6 attempts, 5–80 ms backoff + jitter); lock acquisition has a 10 s deadline (`LockTimeoutError`). SSE tails by offset (no full-file rescan). See `tests/test_transcript_d5a.py`.
