# Deploy TreeFleet on AWS (single EC2 + SSM)

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
| Static 404 after non-editable install | Ensure the built commit includes `ui/static/**/*` in package-data (15.D8) |
| UI unreachable locally | Tunnel must be running; use `http://127.0.0.1:8765` not the public DNS |

## Supply-chain notes

- Cursor installer URL used by bootstrap: `https://cursor.com/install` (run as user `treefleet`, never root). Review that URL before production use.
- GitHub CLI apt keyring from `cli.github.com`.
- `uv` installer from `https://astral.sh/uv/install.sh`.

## IAM and network (as written)

**Instance role:** `AmazonSSMManagedInstanceCore` + inline `ssm:GetParameter|GetParameters|GetParametersByPath` on `arn:...:parameter${ParameterPrefix}*` and `kms:Decrypt` conditioned on `kms:ViaService=ssm.<region>.amazonaws.com`.

**Security group:** **no ingress**. Egress TCP 80 and 443 only.

**IMDSv2:** required on the instance.
