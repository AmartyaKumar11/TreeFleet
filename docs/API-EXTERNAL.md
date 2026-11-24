# TreeFleet external UI API

Stable HTTP surface for a co-located caller (for example PR Sentinel) on the same host. The UI process binds **127.0.0.1** only. US spelling.

All `/api/*` routes require:

- Header `Host: 127.0.0.1` or `Host: localhost` (with or without a port). Any other Host is **403**.
- Header `Authorization: Bearer <token>`.

## Bearer token file

`treefleet ui --token-file PATH` loads an existing token or creates one with `secrets.token_urlsafe(18)` using an atomic create (`O_EXCL`) at mode **0600**. On POSIX an existing file must already be owner-only. The value is never logged. Without `--token-file`, a new random token is generated each process start (browser meta tag only).

Hosted unit: `--token-file /var/lib/treefleet/ui.token`.

## Create a run

`POST /api/runs`

```json
{
  "goal": "string, 10–2000 characters",
  "worker_model": "router",
  "plan_approval": false,
  "repo": "/var/lib/treefleet/repo",
  "base_ref": "HEAD",
  "pr_base": "feature/original-pr-head"
}
```

| Field | Required | Default | Notes |
|---|---|---|---|
| `goal` | yes | — | Max **2000** characters. Screened by the input guard; blocked goals return **400** `input blocked`. |
| `worker_model` | no | `"router"` | Same ids as `GET /api/models`. |
| `plan_approval` | no | `true` | Set `false` to skip the plan gate (Discord Approve already happened). |
| `repo` | no | configured checkout | If present and not the same resolved path as the UI `--repo`, **400** with `this TreeFleet instance serves one repository: <path>`. |
| `base_ref` | no | `"HEAD"` | Branch or ref worktrees start from. Validated as a safe git ref name. When not `HEAD`, the server runs `git fetch origin <base_ref>` then resolves the ref (or `origin/<base_ref>`). Fetch failure → **502**; unresolved after fetch → **400**. |
| `pr_base` | no | `settings.pr.base` | Base branch for `gh pr create --base` for this run only. Same ref-name rules as `base_ref`. |

**200 (ok):** `{"run_id":"<8 hex chars>"}`.

**Ref-name errors:** **400** `invalid base_ref: …` or `invalid pr_base: …`.

Rejected ref shapes include whitespace, `..`, a leading `-`, control characters, `@{`, backslash, and anything `git check-ref-format --allow-onelevel` rejects.

## Approve the plan

`POST /api/runs/{run_id}/approve`

Empty body. Optional header `x-treefleet-client: ui` only changes the recorded source (`ui` vs `http`); it is not authentication.

**200:** `{"ok": true, "source": "http"|"ui"}`.

Not needed when the create call used `plan_approval: false`.

## Poll status

`GET /api/board?run={run_id}`

Fields an external caller should read:

| Field | Meaning |
|---|---|
| `stage` | Pipeline stage (`guard`, `plan`, `approve`, `fleet`, `review`, `integrate`, `done`, …). |
| `tasks` | Per-task rows; use `tasks[].status`, `tasks[].pr_url`. |
| `pr_urls` | Deduplicated PR URLs in task order (convenience; same URLs as on tasks). |
| `report` | Final report JSON when written (`schema_id` `treefleet.final_report.v1`), else `null`. |
| `message` | Outcome message when finished. |
| `needs_human` | Boolean. |
| `finished_at` | Unix time when the run finished, or `null`. |

Polling every 5–15 seconds is enough for a bot; the HTML board uses 1 second.

## Cancel

`POST /api/runs/{run_id}/cancel`

Denies a **waiting plan** only. It does **not** stop workers or `cursor-agent` after the plan is approved. **200:** `{"ok": true, "source": …}`.

## Single repository

This process serves one git checkout (`treefleet ui --repo`). Multi-repo is out of scope for this API revision.
