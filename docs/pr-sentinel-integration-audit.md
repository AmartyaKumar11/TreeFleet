# PR Sentinel ↔ TreeFleet integration audit

Read-only audit. No code was changed for this document beyond creating it.
US spelling. No placeholders.

**PR Sentinel path (workspace):** `D:\PR Sentinel` (git remote `AmartyaKumar11/PR-Sentinel`). Separate repo. It is **not** inside `D:\TreeFleet`. Do not commit it into TreeFleet.

**TreeFleet tip audited:** local `main` / this branch at time of writing.

---

## 1. API surface (TreeFleet UI HTTP)

All routes live in `src/treefleet/ui/server.py`. Uvicorn binds `127.0.0.1` only (`src/treefleet/cli.py` `_ui`, default port 8765). Every `/api/*` call needs `Authorization: Bearer <token>` and a local `Host` (`127.0.0.1` or `localhost`).

| Method | Path | Body / query | Response | Stable for external caller? |
|---|---|---|---|---|
| `POST` | `/api/runs` | JSON: `goal` (str), optional `worker_model` (default `"router"`), optional `plan_approval` (bool, default `true`), optional `repo` (path string; see §5) | `{"run_id": "<8 hex>"}` or 4xx | **Yes** — primary create |
| `POST` | `/api/runs/{run_id}/approve` | empty; optional header `x-treefleet-client: ui` | `{"ok": true, "source": "ui"|"http"}` | **Yes** — plan gate |
| `POST` | `/api/runs/{run_id}/cancel` | empty; same client header | `{"ok": true, "source": ...}` | **Partial** — only denies a waiting plan (see below) |
| `GET` | `/api/board?run=<run_id>` | query `run` | Board snapshot (tasks, stage, report, worktrees, message, …) | **Yes** — poll status |
| `GET` | `/api/runs/{run_id}/events` | SSE; `Last-Event-ID` = byte offset | `text/event-stream` of redacted events | Optional; richer than board |
| `GET` | `/api/transcript?run=&after=&limit=&plan=` | query | Transcript schema JSON | UI-oriented; usable |
| `GET` | `/api/models` | — | `[{id,label},…]` | Optional |
| `GET` | `/api/worktrees` | — | worktree list for current board filter | UI-internal |
| `GET` | `/api/diff?path=&full=&file=` | worktree path | unified diff payload | UI-internal |
| `GET` | `/` | — | HTML with token embedded in meta | UI-only; do not scrape for production |

### (a) Create a run

`POST /api/runs` (`server.py` 492–513):

```json
{
  "goal": "…10–2000 chars…",
  "worker_model": "router",
  "plan_approval": false,
  "repo": "/var/lib/treefleet/repo"
}
```

- Goal is screened by `deterministic_reasons` / `screen_goal` (`guards/input.py`); length cap **2000** (`_LENGTH = 2000`, also `RunRequest.goal` max in `contracts.py` 146).
- Contract `RunRequest` also has `base_ref` (default `"HEAD"`, max 200) but the **HTTP handler never reads `base_ref`** — worktrees always use the board checkout HEAD unless the CLI/pipeline sets it elsewhere.
- Response: `{"run_id": "<token_hex(4)>"}`.

### (b) Approve the plan

`POST /api/runs/{run_id}/approve` (`server.py` 598–607). Empty body. If `plan_approval` was `false` at create, the pipeline never waits and this call is unnecessary (gate may 404 if never registered as waiting).

`x-treefleet-client: ui` only changes the recorded `source` to `"ui"` vs `"http"`; auth is still the bearer.

### (c) Poll status, tasks, integration

`GET /api/board?run=<run_id>` → `Board.snapshot` (`server.py` 265–320):

Notable fields: `stage`, `tasks[]` (from `ui/lanes.py`), `awaiting` (plan cards when waiting), `report` (`report.json` / `FinalReport`), `message`, `needs_human`, `worktrees`, `status_line`, `finished_at`.

Catalog statuses in `run_catalog`: `running` | `done` | `needs` | `error`.

Integration summary: `report.integration` when present (`IntegrationReport` in `contracts.py` 627–637: `branch`, `head_sha`, `merged`, `conflicts`, `checks`, …). PR URLs are **not** on `IntegrationReport`; they appear on task objects via `pr.opened` events (`lanes.py` 243–246: `pr_url`, `pr_number`).

UI board polls every **1s** (`board.js` 1960). External callers can poll slower (5–15s is fine).

### (d) PR URL(s)

No dedicated “get PR URL” endpoint. Read from:

1. `GET /api/board?run=…` → each `tasks[i].pr_url` after `pr.opened`, or
2. SSE `/api/runs/{id}/events` → event type `pr.opened`, payload `{number, url, sha}`, or
3. Events already mirrored in the board snapshot’s `events` tail.

Opened PRs use `gh pr create --base <settings.pr.base> --head tf/<run>/<task>` (`pr/gh.py` 72–75, `pr/stage.py` 102).

### (e) Cancel

`POST /api/runs/{run_id}/cancel` (`server.py` 609–630) only calls `Approval.finish(False)`. If the plan was already approved and the fleet is running, **this does not kill workers or Cursor**. Gap vs Cursor Cloud `POST /agents/{id}/stop`.

### Stability

Treat as a **local machine API** for co-located services: create / approve / board / cancel (plan-only). Diff, worktrees, HTML, and 1Hz board polling habits are UI-internal.

---

## 2. Base branch

### Can a run target a non-default base?

**Partially, and not via the HTTP API today.**

| Layer | Behavior |
|---|---|
| `RunRequest.base_ref` | Exists (`contracts.py` 148); used for worktree creation / diffs (`fleet.py`, `git/worktrees.py`). Default `"HEAD"`. |
| `POST /api/runs` | Does **not** accept or forward `base_ref` (`server.py` 500–512 → `Board.launch` builds `RunRequest` without overriding `base_ref`). |
| Fix PR base | Always `settings.pr.base` from toml (`pr/stage.py` 102; hosted default `main` via `render-config.sh` 12, 59). |

So you cannot today say “open the TreeFleet fix PR against the original PR’s head branch” without changing config or code. The product intent (separate fix PR targeting the original PR head) needs a **per-run PR base**, not only repo default.

### Smallest change (proposal only)

1. Accept optional `base_ref` and `pr_base` on `POST /api/runs`.
2. Pass `base_ref` into `RunRequest` in `Board.launch`.
3. Thread a per-run `pr_base` (or override `PrSettings.base` for that pipeline invocation) into `pr/stage.py` `open_pr(..., base=...)`.
4. Tests: extend UI API tests (e.g. `tests/test_diff_api_d4a2.py` style `TestClient` + `Host: 127.0.0.1`) for request parsing; add a unit test that `drive`/`open_pr` receives the override; keep `tests/test_idle_decide_d8.py` untouched.

Files: `src/treefleet/ui/server.py`, possibly `orchestrator/pipeline.py` / `review.py` where PR stage is invoked, `pr/stage.py`, contracts if a new optional field is preferred on `RunRequest`, plus tests under `tests/`.

### Extra context in the goal

Yes. Put original PR number, title, diff summary, and review findings in the `goal` string (and/or rely on planner task instructions later). Hard caps:

- Goal: **max 2000 characters** (`contracts.py` 146; `guards/input.py` `_LENGTH`).
- Longer review text will be rejected by the deterministic guard before the run starts.
- Catalog truncates displayed goals to 140 chars (`server.py` 362–363); full goal remains in run state / report.

Treat all PR/diff/comment text as **untrusted data**, never as instructions to the orchestrator beyond the goal string (same discipline as AGENTS.md / SECURITY).

---

## 3. Auth

### Bearer

- Generated at process start: `secrets.token_urlsafe(18)` when `create_app` is called without `token=` (`server.py` 380).
- Injected into HTML meta for the browser (`server.py` 435).
- Checked on every `/api` route (`server.py` 417–420).
- **Regenerated on every UI process start.** Not written to disk today. Confirmed earlier in `docs/aws-deploy-audit.md` §Bearer token.

### `x-treefleet-client`

Optional. Only `ui` (case-insensitive) marks approve/cancel `source` as `"ui"`; anything else is `"http"` (`server.py` 605–606, 617–618). Not an auth factor.

### Host gate

`local_host` allows Host values whose hostname is `127.0.0.1` or `localhost` (`server.py` 85–87, middleware 414–416). Another process on the box calling `http://127.0.0.1:8765/...` with `Host: 127.0.0.1` **is accepted**. Non-local Host → 403. This is not a remote IP ACL; bind address already limits exposure to loopback.

### Least-privilege way for PR Sentinel to obtain the token

Preferred (small TreeFleet change, already proposed in aws-deploy-audit):

1. On UI start: load or create `/var/lib/treefleet/ui.token` mode **0600**, owner `treefleet`.
2. Pass `token=` into `create_app`.
3. PR Sentinel runs as the **same** `treefleet` user (or a group with no extra rights beyond read of that one file) and reads the file; never put the bearer into Discord, logs, or SSM.

Avoid stuffing the UI bearer into `/etc/treefleet/env` next to API keys unless necessary — one shared env file widens blast radius. If env is used, a dedicated `TREEFLEET_UI_TOKEN` written by a small helper beside `render-env.sh` is second-best; rotate on UI restart must stay consistent with the running process.

Until persistence exists, a co-located service cannot safely discover the token without scraping `GET /` HTML (fragile) or sharing an injected `token=` via a custom ExecStart (ops-only workaround).

---

## 4. Auto-approve (plan gate)

Supported per run: `POST /api/runs` with `"plan_approval": false` (`server.py` 511–512). Then `Board.launch` passes `approve=None` to `run_pipeline` (`server.py` 251). UI auto-run switch maps to the same flag (`board.js` 1568–1571: `plan_approval: !auto`).

**What PR Sentinel’s Discord “Approve Fix” should map to**

Discord Approve already means “human approved launching a fix.” Map it to:

1. `POST /api/runs` with `plan_approval: false` and a goal built from the composer prompt / findings (≤2000 chars), **not** to TreeFleet’s plan Approve button.
2. Optionally still call TreeFleet approve only if you deliberately keep `plan_approval: true` for a second human gate (not recommended for the Discord UX).

Do **not** confuse TreeFleet plan approval with GitHub/Cursor “approve PR.”

---

## 5. Multi-repo

Hosted UI is started as:

`treefleet ui --repo /var/lib/treefleet/repo` (`deploy/aws/treefleet-ui.service` 26).

CFN `TargetRepoUrl` defaults to `https://github.com/AmartyaKumar11/pr-sentinel-demo` (`template.yaml` 35–37). `render-config.sh` sets `[pr] repo = AmartyaKumar11/pr-sentinel-demo`, `base = main`.

`POST /api/runs` accepts a `repo` path and checks `.git` exists (`server.py` 501–506), but **`Board.launch` always uses `self.repo` from `create_app`** — the payload path is validated and then ignored for the actual pipeline. So today the service is **tied to one project directory** (plus one `[pr].repo` in toml for GitHub).

### Smallest viable design for `AmartyaKumar11/pr-sentinel-demo`

**Short term (demo day):** keep the existing single checkout at `/var/lib/treefleet/repo` pointed at that repo (already the hosted default). PR Sentinel only launches TreeFleet when `task.repo` matches that configured full name; otherwise keep Cursor backend or refuse with a clear Discord message.

**Next smallest:**

1. Maintain clones under `/var/lib/treefleet/repos/<owner>/<name>` (clone/fetch via `gh`/`git` with existing `GH_TOKEN`).
2. Either restart UI with `--repo` for that clone (heavy) **or** teach `Board.launch` / `POST /api/runs` to use the requested local path and a matching `[pr].repo` override per run.
3. Tests: API test that a second repo path is actually used in `RunRequest.repo`; ignore-path / path-guard tests.

---

## 6. Systemd and watchdog

### Adding `pr-sentinel.service` (proposal)

Mirror `treefleet-ui.service`:

- Unit under `deploy/aws/pr-sentinel.service` (new), installed by `bootstrap.sh` beside the UI unit.
- `User=treefleet`, `WorkingDirectory=/var/lib/pr-sentinel`, `EnvironmentFile=-/etc/treefleet/env` (or a rendered `/etc/treefleet/pr-sentinel.env`).
- `ExecStartPre=+/usr/local/lib/treefleet/render-env.sh` (extended) then venv uvicorn: e.g. `/opt/pr-sentinel/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000`.
- `ReadWritePaths` for `/var/lib/pr-sentinel` (SQLite), model cache, etc.
- **Do not** bind `0.0.0.0` on the box; keep loopback like TreeFleet.

### SSM / `render-env.sh` keys

Extend `KNOWN_KEYS` in `deploy/aws/render-env.sh` (47–57) and `put-secrets.ps1`. Proposed names under prefix `/treefleet` (or `/treefleet/pr-sentinel/…` if you want isolation):

| Env var (PR Sentinel) | Proposed SSM | Notes |
|---|---|---|
| `GITHUB_TOKEN` | `/treefleet/GH_TOKEN` (existing) | Map same value to `GITHUB_TOKEN=` in env file; TreeFleet already uses `GH_TOKEN` |
| `DEEPSEEK_API_KEY` | `/treefleet/DEEPSEEK_API_KEY` | Exists |
| `CURSOR_API_KEY` | `/treefleet/CURSOR_API_KEY` | Exists (still needed if `PR_SENTINEL_BACKEND=cursor`) |
| `TYPESAFE_API_KEY` | `/treefleet/TYPESAFE_API_KEY` | Exists |
| `DISCORD_BOT_TOKEN` | `/treefleet/DISCORD_BOT_TOKEN` | **New** |
| `DISCORD_CHANNEL_ID` | `/treefleet/DISCORD_CHANNEL_ID` | **New** |
| `DISCORD_GUILD_ID` | `/treefleet/DISCORD_GUILD_ID` | **New** |
| `DISCORD_OWNER_ID` | `/treefleet/DISCORD_OWNER_ID` | **New** |
| `GITHUB_WEBHOOK_SECRET` | `/treefleet/GITHUB_WEBHOOK_SECRET` | **New** |
| `TREEFLEET_UI_TOKEN` or file | file `/var/lib/treefleet/ui.token` preferred | After TreeFleet persistence change |
| `PR_SENTINEL_BACKEND` | optional plain config, not secret | `cursor` \| `treefleet` |
| `TREEFLEET_BASE_URL` | config | e.g. `http://127.0.0.1:8765` |

### Idle watchdog — what counts as activity

`deploy/aws/idle_decide.py` `decide` keeps the box alive when any of (`idle_decide.py` 22–23):

- `has_ui_connection`
- `has_agent_process`
- `events_recent`
- `boot_grace`

Filled by `idle-watch.sh`:

| Signal | How measured | Discord idle bot? | TreeFleet run from PR Sentinel? |
|---|---|---|---|
| UI connection | `ss` established on **:8765** (lines 18–21) | **No** (bot is Discord WebSocket / :8000) | Only if something holds a TCP client to the UI (board poll or adapter HTTP) |
| Agent process | `pgrep -u treefleet` matching `cursor-agent\|agent --\|treefleet.worker.main` (24–26) | **No** | **Yes** while workers / cursor-agent run |
| Events recent | any `…/runs/**/events.jsonl` mtime &lt; 10 minutes (28–31) | **No** | **Yes** during and briefly after a run |
| Boot grace | uptime &lt; 20 minutes (33–36) | N/A | N/A |

**Confirmed:** a connected-but-idle Discord bot alone does **not** set any keep-alive signal. A TreeFleet run triggered by PR Sentinel **does** (agent process + events.jsonl). Caveat: if the adapter only POSTs once and never polls `:8765`, keep-alive still holds via agent/events; after the run finishes and 10+ minutes pass with no UI TCP, idle timer resumes.

Optional follow-up: treat established TCP on `:8000` as activity if webhook storms should keep the box warm (not required for “run keeps box alive”).

---

## 7. Relay (smee) and security group

### Where to install

Proposal (not implemented):

- Package: Node `smee-client` (or binary) under `/opt/pr-sentinel` or `/usr/local/lib/treefleet`.
- Unit: `smee-relay.service`, `User=treefleet`, `Restart=always`, forwarding the private smee URL to **`http://127.0.0.1:8000/api/webhook/github`** (actual route in PR Sentinel — see §C; not bare `/api/webhook`).
- Supervised by systemd; start after `pr-sentinel.service`.

### Security group

`template.yaml` SecurityGroup: **empty ingress**, egress currently all protocols (`template.yaml` 105–115). Smee is **outbound** HTTPS to smee.io then local HTTP to loopback. **No new inbound rules required.** Confirm after any future tightening of egress that HTTPS to the smee host remains allowed.

---

## 8. Top 5 risks

1. **Security — shared machine token / secrets.** Mitigation: loopback-only binds; 0600 `ui.token` owned by `treefleet`; never log bearer or Discord/GitHub tokens; keep SG ingress empty.
2. **Cost — EC2 + Cursor workers + DeepSeek while Discord is “quiet”.** Mitigation: rely on idle watchdog signals above; budget alarm already in stack; prefer `plan_approval: false` only after Discord Approve so runs are intentional.
3. **Failure mode — cancel does not stop a running fleet.** Mitigation: document Discord `/stop` gap; later add real cancel (kill worker PIDs / run flag) before promising parity with Cursor stop.
4. **Failure mode — base branch / wrong PR target.** Mitigation: implement per-run `pr_base` before switching Discord Approve to TreeFleet; until then only enable backend for demos where `[pr].base` matches intent.
5. **Security — prompt injection via PR text in the goal.** Mitigation: keep 2000-cap + deterministic guard; treat diff/comments as data; never concatenate raw review text into shell; fail closed on guard block and tell Discord.

---

## A. Contract map (PR Sentinel `cursor` ↔ TreeFleet)

PR Sentinel singleton: `cursor = CursorClient()` in `backend/app/services/cursor_client.py` (349).

### Methods and call sites

| Method | Call sites | Fields consumed | TreeFleet equivalent / gap |
|---|---|---|---|
| `launch_on_pull(repo, pr_number, prompt, model=)` | `discord/views.py` `_approve_fix` ~578, re-run ~849, retry ~902; `discord/conversation.py` `_launch` ~600; `agent/orchestrator.py` auto-approve ~674 | Returns `agent_id`, `status`, `model`, `repo`, `branch`, `pr_url`, `head_sha`, `pr_number`; errors `pr_not_launchable` | **Gap:** TreeFleet opens a **new** `tf/…` PR against `settings.pr.base`, does not push onto the existing PR head. Map: `POST /api/runs` → use `run_id` as `agent_id`; `pr_url` appears later on board tasks. Semantics differ by design (separate fix PR). |
| `launch_agent(repo, prompt, model=, branch=)` | `views.py` conflict path ~753; `conversation.py` `_launch` when `params.branch` ~593 | Same launch shape without requiring live PR | Same create-run mapping; branch argument today means “push to existing branch” in Cursor (`_prompt_text` 170–178) — **opposite** of TreeFleet’s new-branch model. Fallback Discord text required. |
| `get_run_status(agent_id)` | `views.py` monitor loops ~337, ~693; reserve checks ~185; `commands.py` `/status` `/stop` `/diff` ~234+; `conversation.py` status/stop/running ~365, 544, 559, 579 | `status` (`completed`/`failed`/`expired`/`running`/`cancelled`), `branch`, `pr_url`, `files_changed`/`files_edited`, `lines_added`/`lines_removed`, `token_usage`, `last_activity`/`current_step`, `created_at`, `result_text` | `GET /api/board?run=<run_id>`: map stage/finished → public status; `tasks[].pr_url`, worktree `committed` for files/lines; **no** `token_usage` or conversation `last_activity` (use status_line / last event types). |
| `cancel_agent(agent_id)` | `commands.py` `/stop` ~260; `conversation.py` `_stop` / relaunch ~451, 562 | bool success | `POST …/cancel` only for plan wait — **gap** for in-flight fleet. |
| `resume_agent(agent_id, message=)` | `commands.py` `/resume` ~287; `conversation.py` `_resume` ~622 | `{status: "running"}` | **No equivalent.** Fallback: Discord “TreeFleet runs cannot take follow-ups; cancel is plan-only; start a new run.” |
| `list_models()` | `commands.py` `/models` ~310; `conversation.py` ~443 | list of model ids | `GET /api/models` → use `id` fields (`router`, `auto`, discovered Cursor models). |
| `default_model` attr | `commands.py` `/model` ~300; conversation set_model ~440 | string | Adapter-local state; pass as `worker_model` on create. |
| Helpers `public_status`, `format_status_reply`, `format_progress`, `model_label`, `run_status_from` | formatting in views/commands/conversation | — | Keep helpers; feed them adapter-normalized dicts. |

### Status vocabulary

Cursor Cloud raw → `public_status` (`cursor_client.py` 14–28): `FINISHED→completed`, `ERROR→failed`, `EXPIRED→expired`, `RUNNING|CREATING→running`, `CANCELLED|STOPPED→cancelled`.

Proposed TreeFleet → same public words:

| TreeFleet signal | public_status |
|---|---|
| `finished` + report / stage `done` without hard error | `completed` |
| catalog `error` / message failure / guard block | `failed` |
| in progress / `needs` (awaiting plan) / fleet | `running` |
| plan cancel / approval denied | `cancelled` |
| (none) | no `expired` unless you invent a wall-clock timeout |

---

## B. Adapter design

### Shape

New module in **PR Sentinel** (not TreeFleet), e.g. `backend/app/services/treefleet_client.py`, exposing the **same async methods** as `CursorClient`, selected by:

`PR_SENTINEL_BACKEND=cursor|treefleet` (default **`cursor`**).

Factory replaces the `cursor` singleton import sites (or keep `cursor = build_backend()` in `cursor_client.py`).

### Must map

- `launch_on_pull` / `launch_agent` → build goal ≤2000 chars from prompt + PR metadata; `POST /api/runs` with `plan_approval=false`, `worker_model` from default/model param; return `agent_id=run_id`.
- `get_run_status` → `GET /api/board?run=`; synthesize `pr_url` from tasks; files/lines from worktrees/`committed`; `last_activity` from `status_line` or last event type string (truncated 500).
- `list_models` → `GET /api/models`.
- `public_status` mapping as above so `monitor_agent` / embeds keep working.

### Cannot map (Discord fallback text)

- **Push onto existing PR head** — TreeFleet always uses `tf/<run>/<task>` and opens a separate PR. Message: “TreeFleet opened a separate fix PR targeting `{pr_base}`; it did not push onto `{original_head}`.”
- **`resume_agent` / follow-up** — “Follow-ups are not supported on the TreeFleet backend; start a new run.”
- **Hard cancel of running workers** — “Stop only cancels a waiting TreeFleet plan; a running fleet may continue until it finishes.”
- **`token_usage`** — omit or show “n/a (TreeFleet)”.
- **`expired`** — omit unless you add a timeout policy.

### Polling cadence

| Caller | Cadence today | TreeFleet capacity |
|---|---|---|
| `monitor_agent` / `monitor_conflict_resolution` | `asyncio.sleep(15)` then status (`views.py` 335, 691) | Fine; board/SSE designed for 1s UI poll |
| UI board | 1s | N/A for Discord |
| Suggested adapter | Keep **15s** Discord polls | Prefer board GET over opening long-lived SSE from Discord tasks |

TreeFleet can sustain far denser polling on localhost; no need to go faster than 15s for Discord.

---

## C. Webhook, smee, Discord lifespan, systemd vs Docker

### HMAC

`routes/webhook.py` 25–31: reads **raw** `await request.body()`, header `X-Hub-Signature-256`, verifies with `verify_hmac` (`utils/hmac_verify.py` 5–11: requires `sha256=` prefix, `hmac.compare_digest`).

**Route path:** `POST /api/webhook/github` (not `/api/webhook`).

### Smee and signature

smee-client forwards the HTTP request including body and headers to the local URL. HMAC over the raw body remains valid **if** the relay does not re-encode JSON. Point smee at `http://127.0.0.1:8000/api/webhook/github`. Confirm once in a dry run with a test delivery; if a proxy buffers/modifies the body, signature checks fail closed (401).

### Discord bot lifespan

`main.py` 25–41: on startup, if `DISCORD_BOT_TOKEN` is set and not a placeholder, `asyncio.create_task(bot.start(token))`; on shutdown `bot.close()` and cancel the task. Same process as FastAPI — suitable for one systemd service running uvicorn.

### Docker → systemd + venv on Ubuntu 24.04

`backend/Dockerfile`: **Python 3.12-slim**, `pip install -r requirements.txt`, `fetch_model.py` for embed models, `DATABASE_PATH=/data/sentinel.db`, uvicorn port 8000.

`requirements.txt`: FastAPI, uvicorn, httpx, discord.py, aiosqlite, onnxruntime, numpy, tokenizers, tree-sitter + language-pack, typesafe-sdk, etc. Prefer binary wheels on amd64; install `build-essential` only if a wheel is missing.

systemd sketch: venv at `/opt/pr-sentinel/.venv`, `DATABASE_PATH=/var/lib/pr-sentinel/sentinel.db`, `EMBED_MODEL_DIR=/var/lib/pr-sentinel/models`, bind **`127.0.0.1:8000`** (tighter than Docker’s `0.0.0.0`).

---

## D. Secrets inventory (PR Sentinel)

From `backend/app/config.py` and `backend/.env.example`:

| Variable | Role | SSM proposal | Status |
|---|---|---|---|
| `GITHUB_WEBHOOK_SECRET` | HMAC | `/treefleet/GITHUB_WEBHOOK_SECRET` | New |
| `GITHUB_TOKEN` | GitHub API | reuse `/treefleet/GH_TOKEN` → also export `GITHUB_TOKEN` | Exists as GH_TOKEN |
| `DEEPSEEK_API_KEY` | LLM | `/treefleet/DEEPSEEK_API_KEY` | Exists |
| `DEEPSEEK_BASE_URL` | LLM URL | config default; optional SSM | Usually non-secret |
| `LLM_MODEL` / `LLM_MAX_TOKENS` / `LLM_TEMPERATURE` | LLM | config / toml | Non-secret |
| `TYPESAFE_API_KEY` / `JEV_API_KEY` | Jev | `/treefleet/TYPESAFE_API_KEY` | Exists |
| `JEV_MODEL` | Jev | config | Non-secret |
| `APP_PORT` | listen | unit file | Non-secret |
| `FRONTEND_URL` / `ALLOWED_ORIGINS` | CORS | config | Non-secret |
| `DATABASE_PATH` | SQLite | unit / env path | Non-secret path |
| `LOG_LEVEL` | logging | config | Non-secret |
| `DISCORD_BOT_TOKEN` | bot | `/treefleet/DISCORD_BOT_TOKEN` | New |
| `DISCORD_CHANNEL_ID` | channel | `/treefleet/DISCORD_CHANNEL_ID` | New |
| `DISCORD_GUILD_ID` | guild | `/treefleet/DISCORD_GUILD_ID` | New |
| `DISCORD_OWNER_ID` | owner | `/treefleet/DISCORD_OWNER_ID` | New |
| `CURSOR_API_KEY` | Cursor Cloud | `/treefleet/CURSOR_API_KEY` | Exists |
| `CURSOR_DEFAULT_MODEL` | default model | config | Non-secret |
| Memory / embed knobs (`MEMORY_*`, `EMBED_*`, `JEV_MEMORY_TIMEOUT_S`) | retrieval | config | Non-secret |
| **New for integration** `PR_SENTINEL_BACKEND` | backend switch | env/config | Non-secret |
| **New** `TREEFLEET_BASE_URL` | UI URL | config | Non-secret |
| **New** UI bearer | auth to TreeFleet | file `ui.token` 0600 | See §3 |

Never commit `.env`. Do not print values in logs or this audit.

---

## Proposed work breakdown (≤4 Cursor tasks)

Ordered. Implement later; this audit does not implement them.

### Task 1 — TreeFleet: durable UI token + optional `base_ref` / `pr_base` on create (Composer 2.5)

**Files:** `src/treefleet/ui/server.py`, `src/treefleet/cli.py` (optional `--token-file`), `deploy/aws/treefleet-ui.service` / bootstrap notes, `pr/stage.py` + call path for per-run base, tests under `tests/` for API parsing and PR base override.

**Tests:** TestClient create with `plan_approval` / `base_ref`; token file load; open_pr receives override.

**Model:** Composer 2.5 (mechanical).

### Task 2 — TreeFleet: honor `repo` path on `POST /api/runs` or document single-repo gate (Sonnet 5.5)

**Judgment:** either implement multi-checkout launch (`Board` uses payload repo + per-run pr.repo) or explicitly reject mismatched `repo` and keep one demo clone.

**Files:** `server.py`, maybe thin clone helper under `deploy/aws/`, tests.

**Model:** Sonnet 5.5 (judgment on multi-repo design), Composer 2.5 for the chosen mechanical slice.

### Task 3 — PR Sentinel: `PR_SENTINEL_BACKEND` adapter (Sonnet 5.5 for contract, Composer 2.5 for glue)

**Files (PR Sentinel repo only):** `treefleet_client.py`, factory in `cursor_client.py`, env in `config.py` / `.env.example`, tests mirroring `tests/test_cursor_launch.py` / day2 status formatting with a fake TreeFleet HTTP.

**Tests:** public_status mapping; launch returns run_id; status poll; resume/cancel fallback strings.

**Model:** Sonnet 5.5 for interface judgment; Composer 2.5 for mechanical wiring.

### Task 4 — Host packaging: `pr-sentinel.service`, smee unit, `render-env.sh` keys (Composer 2.5)

**Files (TreeFleet deploy):** `deploy/aws/pr-sentinel.service`, `smee-relay.service`, extend `render-env.sh` + `put-secrets.ps1` + `bootstrap.sh`, short note in `docs/DEPLOY-AWS.md` / `DEMO-DAY.md`.

**Tests:** offline string/unit checks that new keys appear in render-env known list; no live AWS in the task.

**Model:** Composer 2.5.

---

## Unanswered questions

1. Should TreeFleet fix PRs always target the original PR **head** branch (stacked PR), or sometimes `main`?
2. Is single-repo (`pr-sentinel-demo` only) enough for the first Discord demo, or is multi-repo required on day one?
3. Should Discord `/stop` block until a real TreeFleet fleet-cancel exists, or is “best effort + message” acceptable?
4. Preferred token sharing: `ui.token` file vs `TREEFLEET_UI_TOKEN` in env?
5. Same Linux user `treefleet` for both services, or a separate `pr-sentinel` user with ACL read on `ui.token`?
6. Confirm production webhook path stays `/api/webhook/github` when documenting smee (yes in code today).
7. Auto-approve path in `orchestrator.py` (~671–674): should TreeFleet backend honor Jev auto-approve the same way as Cursor launch?

---

## Files created by this audit

- `docs/pr-sentinel-integration-audit.md` (this file)

No other files created. PR Sentinel was read only; no edits, commits, branches, or installs there.
