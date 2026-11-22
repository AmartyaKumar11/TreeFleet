# 15.D7 — AWS hosting audit (read-only)

**Scope:** One Ubuntu 24.04 EC2 instance, stopped when idle, UI only via SSM Session Manager port-forward to `127.0.0.1:8765`. No AWS CLI was run. No code or config was changed for this audit.

**Date:** 2026-11-22  
**Host audited from:** Windows (`D:\TreeFleet`), git `2.50.0.windows.2`, `cursor-agent` `2026.11.05-86047df`, `gh` logged in via keyring (token not recorded).

---

## 1. Windows-isms

| Location | Windows behavior | Linux behavior | Severity |
|---|---|---|---|
| `src/treefleet/events.py:191-226` | File lock via `msvcrt.locking` (`LK_NBLCK` / `LK_UNLCK`) | `fcntl.flock` (`LOCK_EX\|NB` / `LOCK_UN`) | Harmless (both paths exist; Linux uses fcntl) |
| `src/treefleet/cursor_run.py:118-119` | Adds `--sandbox disabled` | Omits the flag (sandbox available on Linux/macOS per S10b / docs) | Degrades (Linux can use sandbox; current code does not enable it on Linux either — only disables on Windows) |
| `src/treefleet/cursor_run.py:126-157` | Resolves `cursor-agent.cmd`/`.ps1` → bundled `versions/*/node.exe` + `index.js` | Returns `[str(target)]` for a real binary / shim on PATH | Harmless if Linux install is a normal executable; **breaks** if only a shell script is found and `shell=False` cannot exec it without a shebang path |
| `src/treefleet/cursor_run.py:160-167` | Minimal env keeps `SYSTEMROOT`, `PATHEXT`, `TEMP`, `TMP` | Same keys usually empty/unused; `PATH` still required | Harmless |
| `src/treefleet/worker/spawner.py:21-35,57-71` | `_KEEP` includes `SystemRoot`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `COMSPEC`, … | Those vars absent; Linux still gets `PATH`, `TEMP`/`TMP` if set, plus keys | Harmless if `PATH` and secrets are set |
| `src/treefleet/worker/spawner.py:154-166` | `creationflags=0x4` (`CREATE_SUSPENDED`) then job bind + resume | Not used | Harmless (Linux branch at `:167-178`) |
| `src/treefleet/worker/spawner.py:167-178` | n/a | `start_new_session=True` + `preexec_fn=_linux_pdeathsig` | Harmless (intended Linux path) |
| `src/treefleet/procgroup.py:3-10,27-167` | Win32 job object kill-on-close; `resume_process` / `bind_worker` | Job APIs are no-ops; Linux uses prctl PDEATHSIG in spawner | Degrades: grandchildren (e.g. `cursor-agent`) can outlive the worker on Linux (`docs/SESSIONS.md`) |
| `src/treefleet/procgroup.py:170-177` | n/a | `prctl(PR_SET_PDEATHSIG, 9)` via `libc.so.6` | Harmless on Linux; would break on non-glibc if ever targeted |
| `src/treefleet/config.py:160-163` | Venv python = `Scripts/python.exe` | `bin/python` | Harmless |
| `src/treefleet/config.py:201-203` | Reads `paths.worktree_windows` | Reads `paths.worktree_other` | Harmless if toml set; **breaks** if only Windows root is configured |
| `treefleet.toml:38-39` | `worktree_windows = 'C:\wt'` | `worktree_other = "/tmp/tf-wt"` | Degrades: `/tmp` is wiped on many AMIs/reboots — worktrees not durable unless moved to EBS path |
| `src/treefleet/onboarding.py:349-402,419-421,659-661` | Defaults / validates `C:\wt`; activate via `Scripts\` | Defaults `/tmp/tf-wt`; POSIX activate | Harmless (platform-aware) |
| `src/treefleet/git/diff_view.py:49` | Null device `NUL` | `/dev/null` | Harmless |
| `src/treefleet/git/merge.py:24-26` | Default merge root `C:\tf` | `/tmp/tf` | Degrades (tmp volatility) if that helper is used |
| `src/treefleet/git/worktrees.py:60-61` | `os.path.normcase` for path equality | Same API; on Linux case is preserved so case-mismatched paths do **not** collapse | Degrades / can **break** proofs if a tool emits differently cased paths on a case-sensitive FS |
| `src/treefleet/git/worktrees.py:13,91-94` | Cap worktree path length at **120** chars | Same cap (short path still required) | Harmless if root stays short (`/var/lib/treefleet/wt/...`) |
| `src/treefleet/git/worktrees.py:64-65` | Forces `core.autocrlf=false` | Same | Harmless (good for Linux) |
| `src/treefleet/git/worktrees.py:156-171` | Retries `worktree remove` for Windows directory locks | Same retry loop | Harmless |
| `src/treefleet/checks.py:26-30` | Escapes `\` before `shlex.split` | Plain `shlex.split` | Harmless |
| `src/treefleet/checks.py:40-54` | Rewrites `npm`/`npx` `.cmd` → `node` + cli.js | No rewrite | Harmless |
| `src/treefleet/mcp_servers/worktree_fs_server.py:285-300` | Requires `SystemRoot`, `TEMP`, `TMP` | Skips those hard requirements | Harmless |
| `src/treefleet/ui/server.py:85-87` | Host gate allows `127.0.0.1` / `localhost` only | Same | Harmless (desired) |
| Hard-coded `D:\` in production Python | None found under `src/` | n/a | Harmless |
| `CTRL_C_EVENT` / `SIGBREAK` / `taskkill` / `powershell` / `cmd.exe` launches | Not found as process-control APIs in `src/` (job object / `psutil.kill` used instead) | `kill_tree` via psutil (`git/worktrees.py:142-153`) | Harmless |
| Line endings | Reads/writes UTF-8 with `errors="replace"`; git autocrlf forced false | Same | Harmless |

---

## 2. Runtime dependencies (Linux)

**Python:** `requires-python = ">=3.12"` (`pyproject.toml:9`).

**Base deps:** pydantic, httpx, mcp, psutil, langfuse, opentelemetry-api/sdk (`pyproject.toml:10-18`).

**Orchestrator extra:** google-adk, a2a-sdk==0.3.26, litellm, openinference-instrumentation-google-adk, fastapi, uvicorn[standard] (`pyproject.toml:24-31`).

**Worker extra:** crewai[a2a], a2a-sdk==0.3.26, litellm, openinference-instrumentation-crewai (`pyproject.toml:32-37`).

**Git:** Uses `git worktree add -b`, `list --porcelain`, `remove --force`, `prune`, `clone`, `status --porcelain`, local `config`. These are long-stable; recommend **git ≥ 2.25** (Ubuntu 24.04 default is fine). No exotic worktree flags observed.

**GitHub CLI (`gh`):** `src/treefleet/pr/gh.py` runs `gh` with `shell=False`, explicit `--repo`, UTF-8. Auth is whatever `gh auth login` stored (host keyring / `GH_TOKEN` / `GITHUB_TOKEN`). Preflight fails closed if unauthenticated (`onboarding.py` / `pr/preflight.py`).

**cursor-agent on Linux:**

- Install (official): `curl https://cursor.com/install -fsS | bash` → typically `~/.local/bin` (`agent` / `cursor-agent`).
- Bundles/uses Node internally on Windows (`.cmd` → `node.exe`); Linux install is expected to be a self-contained CLI (still Node-based under the hood per Cursor docs). TreeFleet does not require a separate system Node if the CLI ships its own runtime.
- Headless flags TreeFleet uses: `--print`, `--output-format stream-json`, `--stream-partial-output`, `--workspace`, `--trust`, optional `--model` (`cursor_run.py:106-123`).
- **Auth:** `CURSOR_API_KEY` env (preferred for headless) or `--api-key` (confirmed in local `--help` and [Cursor CLI auth docs](https://cursor.com/docs/cli/reference/authentication)). Browser `agent login` is unsuitable for a headless EC2 without a one-time SSM browser dance (`NO_OPEN_BROWSER=1`).
- TreeFleet already forwards `CURSOR_API_KEY` into the cursor child (`cursor_run.py:169-171`) and worker env (`spawner.py:64-67`).

**Outbound network the box needs:** DeepSeek API, Typesafe/Jev if used, Langfuse if configured, Cursor API (`*.cursor.sh` / `*.cursorapi.com` per Cursor docs), GitHub API for `gh`.

---

## 3. State and network

### Where state is written

| Path | Role | Persist on EBS? |
|---|---|---|
| Repo clone (e.g. `/var/lib/treefleet/repo`) | Source of truth + `.git` | **Yes** |
| `runs/` under cwd / `create_app(..., runs=)` (`server.py:380`) | `run.json`, `events.jsonl`, worker logs | **Yes** |
| `treefleet.toml` (`config.py:175-182`, override `TREEFLEET_CONFIG`) | Non-secret settings | **Yes** |
| `paths.worktree_other` (default `/tmp/tf-wt`) | Git worktrees | **Yes — change off `/tmp`** |
| Venvs `.venv-orch` / `.venv-worker` | Interpreters | Yes (or rebuild at boot) |
| UI bearer token | In-memory only today | See below |
| Secrets | Env only (`DEEPSEEK_API_KEY`, `TYPESAFE_API_KEY`, `CURSOR_API_KEY`, Langfuse keys) | **SSM Parameter Store / Secrets Manager → env file**, never AMI |

### Bind host and port

- CLI: `uvicorn.run(app, host="127.0.0.1", port=parsed.port)` with default port **8765** (`cli.py:184-205`).
- Host middleware rejects non-local `Host` headers (`server.py:85-87`, middleware ~414+).
- Workers also bind `127.0.0.1` (`worker/main.py:51`).
- **Confirmed:** default cannot bind `0.0.0.0`; there is no CLI flag to change the UI bind host.

### Bearer token

- Created as `secrets.token_urlsafe(18)` when `create_app` is called without `token=` (`server.py:380`).
- Injected into `board.html` as `<meta name="tf-token">` (`server.py:435`, `board.html:6`).
- Required as `Authorization: Bearer …` on `/api/*` (`server.py:419`).
- **Not** written to disk and **not** printed by `treefleet ui` today.
- **Safe headless retrieval (proposal):** at boot, generate or load a token into a `0600` file e.g. `/var/lib/treefleet/ui.token`, pass it into `create_app(token=...)` (small code change in a later build task), and inside an SSM session run `sudo -u treefleet cat /var/lib/treefleet/ui.token` then port-forward. Until that exists, scrape is only possible by reading the HTML meta over the forwarded port (awkward) or attaching a debugger — treat as a **build-task gap**.

---

## 4. Packaging (wheel)

Built: `spikes/ui-d7-audit/wheel/treefleet-0.1.0-py3-none-any.whl` via `python -m build --wheel` (91 entries listed in `spikes/ui-d7-audit/wheel-listing.txt`).

**Verdict: YES — `ui/static/**` is missing from a non-editable install.**

Wheel includes `treefleet/ui/board.html` and Python modules under `ui/`, but **no** `static/js`, `static/css`, or fonts.

Cause: `pyproject.toml:46-47`:

```toml
[tool.setuptools.package-data]
treefleet = ["ui/*.html", "sample/templates/**/*"]
```

`server.py:28` serves `_STATIC = Path(__file__).with_name("static")`, which only works for editable installs / source trees.

**Exact fix (do not apply in this task):**

```toml
[tool.setuptools.package-data]
treefleet = ["ui/*.html", "ui/static/**/*", "sample/templates/**/*"]
```

(Optionally add a smoke test that imports `treefleet.ui.server` from an installed wheel and asserts `(Path(treefleet.ui.server.__file__).parent / "static" / "js" / "board.js").is_file()`.)

Until fixed, EC2 must use **editable install from a git checkout** on the EBS volume, or vendor static files another way.

---

## 5. Linux test signal

**Not verified.**

- WSL lists only `docker-desktop` (Stopped); no Ubuntu WSL distro.
- Docker engine socket unavailable (`dockerDesktopLinuxEngine` pipe missing).

**Needed:** Ubuntu 24.04 via WSL2 or a running Docker engine, then e.g.:

```bash
uv venv .venv-orch && uv pip install -p .venv-orch -e ".[orchestrator,dev]"
uv venv .venv-worker && uv pip install -p .venv-worker -e ".[worker]"
# non-UI subset example:
.venv-orch/bin/python -m pytest -q -m "not ui and not live"
```

---

## 6. Proposed layout (proposal only)

### Instance

| Item | Proposal |
|---|---|
| AMI | Ubuntu 24.04 LTS |
| Size | **t3.medium** for course demos; **t3.large** if parallel Cursor workers + ADK feel tight |
| Root volume | **40 GB gp3** (repo, two venvs, runs, worktrees, Cursor CLI) |
| Public IP | Optional for outbound only; **no inbound SG rules** |
| Access | SSM Session Manager only (instance profile) |

### Directories

```
/opt/treefleet/                 # git checkout (or release tag)
/var/lib/treefleet/repo/        # working clone used by UI
/var/lib/treefleet/runs/        # event logs
/var/lib/treefleet/wt/          # worktree root (NOT /tmp)
/var/lib/treefleet/ui.token     # 0600 bearer (after build change)
/etc/treefleet/treefleet.toml   # TREEFLEET_CONFIG
/etc/treefleet/env              # 0600, rendered from SSM at boot
```

Set `paths.worktree_other = "/var/lib/treefleet/wt"` in toml on the instance.

### systemd unit (sketch)

```ini
[Unit]
Description=TreeFleet UI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=treefleet
Group=treefleet
WorkingDirectory=/var/lib/treefleet/repo
EnvironmentFile=/etc/treefleet/env
Environment=TREEFLEET_CONFIG=/etc/treefleet/treefleet.toml
ExecStart=/opt/treefleet/.venv-orch/bin/treefleet ui --repo /var/lib/treefleet/repo --port 8765
Restart=on-failure
# Logs → journald

[Install]
WantedBy=multi-user.target
```

Boot snippet (cloud-init or ExecStartPre): pull SSM parameters into `/etc/treefleet/env` (`DEEPSEEK_API_KEY`, `TYPESAFE_API_KEY`, `CURSOR_API_KEY`, optional Langfuse / `GH_TOKEN`), `chmod 0600`.

### `up` / `down` helpers (operator laptop)

```bash
# up.sh
aws ec2 start-instances --instance-ids "$INSTANCE_ID"
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
echo "aws ssm start-session --target $INSTANCE_ID --document-name AWS-StartPortForwardingSession --parameters '{\"portNumber\":[\"8765\"],\"localPortNumber\":[\"8765\"]}'"
```

```bash
# down.sh
aws ec2 stop-instances --instance-ids "$INSTANCE_ID"
```

### Idle auto-stop

- Prefer **CloudWatch alarm** on CPUUtilization low for N minutes → EC2 Stop action; or
- In-instance timer: if no `runs/*/events.jsonl` mtime and no SSM sessions for 30–60 minutes → `shutdown -h now`.
- Document that auto-stop kills in-flight Cursor runs.

### IAM instance role (minimal)

- `ssm:UpdateInstanceInformation`, `ssmmessages:*`, `ec2messages:*` (SSM agent)
- `ssm:GetParameter(s)` on `/treefleet/*` (or Secrets Manager `GetSecretValue`)
- **No** `ec2:AuthorizeSecurityGroupIngress`, no S3 admin, no IAM write

### Budget

- AWS Budgets alert at e.g. **$15/month** and **$30/month** on the account/tag `project=treefleet`.

### Security group

- **Inbound: none**
- Outbound: HTTPS (443) to internet (or tighter prefixes for GitHub, DeepSeek, Cursor if known)

### Rough monthly cost (estimates — verify on AWS pricing page; assume us-east-1 on-demand, 40 GB gp3 ≈ $3.20/mo)

| Scenario | t3.medium | t3.large |
|---|---|---|
| Stopped all month (EBS only) | ~$3.20 | ~$3.20 |
| 20 hours running + EBS | ~$4.00 | ~$4.90 |
| 100 hours running + EBS | ~$7.40 | ~$11.50 |

Assumptions: t3.medium ≈ $0.0416/hr, t3.large ≈ $0.0832/hr, gp3 ≈ $0.08/GB-month. Ignores SSM free tier, NAT, and Cursor/DeepSeek API spend (those dominate if you run live agents).

---

## 7. Risks and open questions (ranked)

1. **Wheel omits `ui/static`** — non-editable install serves a broken UI. Must fix package-data or deploy editable/source.
2. **Live Cursor on EC2** depends on a valid `CURSOR_API_KEY`, outbound Cursor API access, and team policy that still allows headless/`--print` (Cursor can disable headless for a team). Without that, coding tasks cannot run.
3. **UI token not persisted** — headless operator cannot safely discover the bearer without a small code/ops change.
4. **Worktree root `/tmp/tf-wt`** — lost on reboot; must point at EBS.
5. **Linux process hygiene** — PDEATHSIG does not kill `cursor-agent` grandchildren; orphaned Node processes possible after crashes (`docs/SESSIONS.md`).
6. **Case-sensitive filesystem** — `normcase` equality is weaker on Linux; path mismatches may fail proofs.
7. **PR/demo path** — `gh` needs auth on the box; course may also require human PR review on GitHub (public URL of the PR exists, but TreeFleet UI stays private).
8. **Cost runaway** — forgotten running instance + Cursor API usage; mitigate with budget + auto-stop.

### Demo fallback

If live Cursor cannot authenticate on the instance: run **replay / stub fleet** UI (`TREEFLEET_STUB_FLEET=1` / `--replay`) for the board demo, and run one live Cursor task only from the Windows laptop against `pr-sentinel-demo` as already constrained. Say explicitly in the evaluation that AWS hosts the UI + orchestration shell, with coding exercised where Cursor auth works.

---

## Questions before the build task

1. Confirm **region** and whether **t3.medium** is acceptable for the course.
2. Will the evaluator require a **live Cursor edit on the EC2 box**, or is replay/stub UI enough for the AWS check?
3. Do you already have a **Cursor user API key** usable on a headless Linux host (and is team “Disable CLI headless mode” off)?
4. Preferred secret store: **SSM Parameter Store** vs Secrets Manager?
5. Should the build task **fix the wheel package-data** and add a **token file / `--token-file`** for `treefleet ui`?
6. Worktree root path preference under `/var/lib/treefleet/…`?
7. Is creating/keeping the accidental repo-root **`.venv`** (from `uv run` during this audit) OK to delete later, or leave it?

---

## Files created by this audit

- `docs/aws-deploy-audit.md` (this file)
- `spikes/ui-d7-audit/wheel/treefleet-0.1.0-py3-none-any.whl`
- `spikes/ui-d7-audit/wheel-listing.txt`

**Unexpected (not deleted):** repo-root `.venv/` created by `uv run` while building the wheel. Confirm before removal.
