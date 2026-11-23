# Deploy TreeFleet on AWS (single EC2 + SSM)

Demo-day checklist (rehearsal, timings, failure playbook): [`docs/DEMO-DAY.md`](DEMO-DAY.md).

This runbook deploys **nothing by itself**. You run the commands. The UI stays on `127.0.0.1:8765` on the instance and is reached only through an SSM Session Manager port-forward. **Anyone with `ssm:StartSession` on the instance can reach the UI** — keep the AWS account locked down.

Assets live under `deploy/aws/`. Design basis: `docs/aws-deploy-audit.md`.

## Prerequisites (operator laptop)

- AWS account with permission to create EC2, IAM, Budgets, and SSM parameters
- AWS CLI v2
- [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
- SSO or access keys configured (`aws configure sso` or env vars)
- PowerShell 5+ (Windows) for `*.ps1`
- Git + network to clone this repo

## Ordered deploy commands

Set these once per shell (replace placeholders):

```powershell
$env:AWS_REGION = "us-east-1"          # your region
$env:AWS_PROFILE = "your-profile"      # optional
$env:PARAMETER_PREFIX = "/treefleet"
```

### 1. Look up VPC and subnet

```powershell
aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query "Vpcs[0].VpcId" --output text --region $env:AWS_REGION
# then:
aws ec2 describe-subnets --filters Name=vpc-id,Values=<VpcId> Name=default-for-az,Values=true --query "Subnets[0].SubnetId" --output text --region $env:AWS_REGION
```

### 2. Deploy the stack

```powershell
aws cloudformation deploy `
  --stack-name treefleet `
  --template-file deploy/aws/template.yaml `
  --capabilities CAPABILITY_NAMED_IAM `
  --region $env:AWS_REGION `
  --parameter-overrides `
    VpcId=vpc-xxxxxxxx `
    SubnetId=subnet-xxxxxxxx `
    InstanceType=t3.large `
    TreeFleetRepoUrl=https://github.com/<you>/TreeFleet.git `
    TreeFleetRepoRef=main `
    TargetRepoUrl=https://github.com/AmartyaKumar11/pr-sentinel-demo `
    ParameterPrefix=/treefleet `
    IdleMinutes=45 `
    MaxUptimeHours=12 `
    BudgetEmail=you@example.com `
    MonthlyBudgetUsd=15
```

Capture the instance id:

```powershell
$env:INSTANCE_ID = aws cloudformation describe-stacks --stack-name treefleet --query "Stacks[0].Outputs[?OutputKey=='InstanceId'].OutputValue" --output text --region $env:AWS_REGION
```

First boot runs `bootstrap.sh` (apt, uv, venvs, cursor-agent, systemd). Give it **15–25 minutes**, then confirm SSM Online (`.\deploy\aws\up.ps1` waits for that after start).

### 3. Put secrets

```powershell
.\deploy\aws\put-secrets.ps1 -Prefix /treefleet
```

Prompts (SecureString, blank = skip):

| Parameter key | Becomes env var |
|---|---|
| `DEEPSEEK_API_KEY` | `DEEPSEEK_API_KEY` |
| `TYPESAFE_API_KEY` | `TYPESAFE_API_KEY` |
| `CURSOR_API_KEY` | `CURSOR_API_KEY` |
| `LANGFUSE_PUBLIC_KEY` | `LANGFUSE_PUBLIC_KEY` |
| `LANGFUSE_SECRET_KEY` | `LANGFUSE_SECRET_KEY` |
| `LANGFUSE_BASE_URL` | `LANGFUSE_BASE_URL` |
| `GH_TOKEN` | `GH_TOKEN` (optional; private clones / gh) |
| `TREEFLEET_CURSOR_SANDBOX` | `TREEFLEET_CURSOR_SANDBOX` (set to `disabled` if Cursor sandbox fails on Ubuntu) |

Restart the UI after secrets change: SSM shell → `sudo systemctl restart treefleet-ui`.

### 4. Up, tunnel, smoke, down

```powershell
.\deploy\aws\up.ps1
.\deploy\aws\tunnel.ps1
# other window:
.\deploy\aws\smoke.ps1
# optional longer check:
.\deploy\aws\smoke.ps1 -Tests
# when finished:
.\deploy\aws\down.ps1
```

Browse **http://127.0.0.1:8765** while the tunnel is up. The page embeds the bearer token in a meta tag; the Host gate remains localhost-only.

## Cost (estimates — verify on the AWS pricing page)

Assumes us-east-1 on-demand, **40 GiB gp3** (~$3.20/mo), ignores Cursor/DeepSeek API spend.

| Scenario | t3.medium | t3.large |
|---|---|---|
| Stopped all month (EBS only) | ~$3.20 | ~$3.20 |
| 20 hours running + EBS | ~$4.00 | ~$4.90 |
| 100 hours running + EBS | ~$7.40 | ~$11.50 |

## Idle watchdog

`treefleet-idle.timer` runs every 5 minutes. It **keeps** the instance awake when any of these is true:

- Established TCP to `127.0.0.1:8765` (SSM tunnel / local client)
- A `treefleet` agent/worker process is running
- Any `runs/*/events.jsonl` modified in the last 10 minutes
- Uptime under 20 minutes (boot grace)

Otherwise, after **IdleMinutes** (default 45) of inactivity it runs `systemctl poweroff` (**stop**, not terminate). Independently, if uptime exceeds **MaxUptimeHours** (default 12) **and** there is no established UI connection, it powers off.

**In-flight Cursor runs are killed when the instance stops.** Save work or call `down.ps1` only when idle.

## Demo fallback (honest for evaluation)

If live Cursor auth is unavailable on the box, demonstrate the board with:

```bash
# on-instance example
TREEFLEET_STUB_FLEET=1 /opt/treefleet/.venv-orch/bin/treefleet ui --repo /var/lib/treefleet/repo --port 8765
# or --replay against a recorded events.jsonl
```

Say clearly: AWS hosts the private UI and orchestration shell; coding may be shown from a laptop session where Cursor is already authenticated, or via stub/replay on the instance.

## Teardown (text only — you run it)

1. `.\deploy\aws\down.ps1` (optional; stack delete stops/terminates per template)
2. `aws cloudformation delete-stack --stack-name treefleet --region $env:AWS_REGION`
3. Delete SSM parameters under `/treefleet` if you no longer need them
4. Cancel the Budgets subscriber email if prompted

## Troubleshooting

| Symptom | What to try |
|---|---|
| SSM not Online | Wait for bootstrap; check instance profile; ensure subnet has outbound HTTPS; CloudWatch / console system log |
| Session Manager plugin missing | Install the plugin; `session-manager-plugin` on PATH |
| cursor-agent auth errors | Put `CURSOR_API_KEY` via `put-secrets.ps1`; restart UI |
| Cursor sandbox fails on Ubuntu 24.04 | Set parameter `TREEFLEET_CURSOR_SANDBOX=disabled`; restart UI |
| Clone failures | Put `GH_TOKEN` for private repos; check `TargetRepoUrl` / `TreeFleetRepoUrl` |
| `git push` 403 / `PrSafetyError: git push failed` | Ensure `GH_TOKEN` in SSM; `sudo /usr/local/lib/treefleet/setup-host.sh`; confirm `git config --global --get-regexp credential` as `treefleet`; restart UI. If helper is set but push still 403 with `Resource not accessible by personal access token`, the token lacks **Contents: Write** on the target repo (fine-grained) or classic `repo` scope — re-run `put-secrets.ps1` with a writable token (operators only; agents must not `ssm put-parameter`) |

| `--print` hangs with 0 bytes | Confirm SG allows all outbound (not 80/443-only); see D10 notes |
| Static 404 after non-editable install | Ensure the built commit includes `ui/static/**/*` in package-data (15.D8) |
| UI unreachable locally | Tunnel must be running; use `http://127.0.0.1:8765` not the public DNS |

## Supply-chain notes

- Cursor installer URL used by bootstrap: `https://cursor.com/install` (run as user `treefleet`, never root). Review that URL before production use.
- GitHub CLI apt keyring from `cli.github.com`.
- `uv` installer from `https://astral.sh/uv/install.sh`.

## IAM and network (as written)

**Instance role:** `AmazonSSMManagedInstanceCore` + inline `ssm:GetParameter|GetParameters|GetParametersByPath` on `arn:...:parameter${ParameterPrefix}*` and `kms:Decrypt` conditioned on `kms:ViaService=ssm.<region>.amazonaws.com`.

**Security group:** **no ingress**. **Egress: all outbound** (`IpProtocol: -1` to `0.0.0.0/0`). Inbound stays empty.

**IMDSv2:** required on the instance.

## D10 operational notes (live AWS box)

### Why all outbound egress

`cursor-agent --print` opens **TCP/22** (observed under strace/tcpdump toward a Cursor-related host) in addition to HTTPS. A security group limited to TCP 80/443 black-holes that connect (`EINPROGRESS` until timeout) and `--print` hangs with **0 bytes** of I/O even though `cursor-agent about` / `list-models` (443-only) succeed. Keep **zero inbound** rules; allow all outbound.

### HOME / stdin / XDG for cursor-agent

Headless `--print` needs a real `HOME` (and usual `XDG_*`) so the CLI can read its local auth store, and **stdin must be `/dev/null`** (an open pipe hangs the CLI). TreeFleet’s `cursor_run` passes a minimal env that keeps `HOME`/`XDG_*`/`CURSOR_API_KEY` and always sets `stdin=DEVNULL`.

### GitHub token (`GH_TOKEN`)

Use a **fine-grained** personal access token scoped to the **single target demo repo** (for this stack: `AmartyaKumar11/pr-sentinel-demo`).

| Permission | Access |
|---|---|
| **Contents** | Read and write |
| **Pull requests** | Read and write |
| **Metadata** | Read (automatic) |

Suggested expiry: **30 days**. Rotate with `.\deploy\aws\put-secrets.ps1` (updates `/treefleet/GH_TOKEN`), then `sudo systemctl restart treefleet-ui` so `render-env.sh` reloads the value and `setup-host.sh` re-applies the credential helper.

**Warning:** `git ls-remote` on a **public** repo succeeds without write rights and proves nothing about push. Prove write auth with a dry-run push (for example `git push --dry-run origin HEAD:refs/heads/tf-smoke-dryrun`); `deploy/aws/smoke.sh` runs that check and fails with `GH_TOKEN is read-only or missing Contents/Pull requests write` when the token cannot push.

### Git credential helper (`gh auth setup-git`)

`git push` for `tf/<run>/…` branches uses the plain `origin` HTTPS URL (never a token-in-URL). Auth is via the **`gh` Git credential helper**, which reads `GH_TOKEN` from the process environment (sourced from `/etc/treefleet/env`, mode 0600). `deploy/aws/setup-host.sh` runs `gh auth setup-git` as user `treefleet` (idempotent). `treefleet-ui.service` runs that script in `ExecStartPre` **after** `render-env.sh` so a token added later still gets a helper. Verify helper config with `git config --global --get-regexp credential` as `treefleet`; verify **write** with the dry-run above (not `ls-remote` alone).

### WARNING — live stack drift (do not CloudFormation-update this stack)

The **live** security group was switched to all-egress **out-of-band** (EC2 authorize/revoke) after CloudFormation change sets showed **Conditional Replacement** on the instance (`ImageId` from the SSM dynamic AMI resolve) plus the SG egress edit. **Do not run `aws cloudformation deploy` / execute-change-set on this stack** until the AMI is pinned to the running image; an unpinned update can replace the instance. Template `deploy/aws/template.yaml` already encodes all-egress for a future clean redeploy with a pinned AMI.

### Live vs stub

`render-env.sh` exports real `DEEPSEEK_API_KEY` / `CURSOR_API_KEY` from SSM when both are present and **does not** set `TREEFLEET_STUB_FLEET`. Stub/placeholder mode applies only when those keys are missing. Restart the UI after `put-secrets.ps1` so `ExecStartPre` re-renders env.

### `cursor-agent --print` exit quirk

After a successful edit, `--print` sometimes **fails to exit** (outer `timeout` → **rc=124**) even though files and stdout are already written. TreeFleet treats the worktree/git result as source of truth, not the process exit code alone; wrap long calls with an explicit timeout and inspect the worktree.
