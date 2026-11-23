# Demo-day runbook (AWS EC2 + SSM)

Operator checklist for rehearsing and presenting TreeFleet on the hosted EC2 box. US spelling. The UI is private (`127.0.0.1:8765`); reach it only through an SSM port-forward. Deeper deploy detail: [`docs/DEPLOY-AWS.md`](DEPLOY-AWS.md).

## One-time prerequisites

On the operator laptop:

- AWS CLI v2
- [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
- AWS profile **`treefleet`**, region **`ap-south-1`**
- PowerShell 5+ (for `deploy/aws/*.ps1`)
- Git access to this repo

Set these for every session:

```powershell
$env:AWS_PROFILE = "treefleet"
$env:AWS_REGION = "ap-south-1"
$env:INSTANCE_ID = "i-0db26e98e83a8cca2"
```

(`AWS_DEFAULT_REGION` may also be set to `ap-south-1`; the scripts use the profile and region above.)

## Day-before rehearsal checklist

1. Log in: `aws login --region ap-south-1 --profile treefleet` (or your usual SSO/login flow for that profile).
2. Start the box: `.\deploy\aws\up.ps1` (waits until SSM is Online).
3. In a **separate** window: `.\deploy\aws\tunnel.ps1`.
4. Open **http://127.0.0.1:8765** in a browser.
5. Run `.\deploy\aws\smoke.ps1`. Expect every check green, including the **git write-auth dry-run** (proves `GH_TOKEN` can push; `ls-remote` alone is not enough on a public repo).
6. Do **one** live run with a one-line goal (for example: add a short Usage section to the README). Approve the plan in the UI. Confirm a pull request appears on `AmartyaKumar11/pr-sentinel-demo`.
7. Close the tunnel window (Ctrl+C).
8. Stop: `.\deploy\aws\down.ps1`. Confirm the script prints that the instance is **stopped**.

Do not merge the rehearsal PR unless you intend to. Do not touch historical PRs you are not evaluating.

## Demo-day script (timings)

| When | Action |
|---|---|
| **T−10 min** | `.\deploy\aws\up.ps1`. Boot grace is **20 minutes**; idle shutoff is **45 minutes** with no UI tunnel / agent activity; hard uptime cap is **12 hours**. |
| **T−5 min** | `.\deploy\aws\tunnel.ps1` in a second window; open http://127.0.0.1:8765; spot-check that the board loads. |
| **Live** | Show the panel: UI over the tunnel → goal → plan → **Approve** → live agent work in a worktree → PR on GitHub. |
| **After** | Close the tunnel; `.\deploy\aws\down.ps1`; confirm **stopped**. |

**What to say about the architecture (short):**

- One EC2 instance with a security group that has **zero inbound** rules; you never open SSH or a public HTTP port.
- Operators reach the UI only via **SSM Session Manager** port-forward to `127.0.0.1:8765`.
- Secrets (`DEEPSEEK_API_KEY`, `CURSOR_API_KEY`, `GH_TOKEN`, …) live in **SSM Parameter Store** and are rendered into `/etc/treefleet/env` (mode 0600) on UI start — not baked into the AMI.
- **Auto-stop**: idle watchdog and max-uptime power the instance off so a forgotten tunnel does not burn the month.

## Failure playbook

| Symptom | What to do |
|---|---|
| Login expired | Re-run `aws login --region ap-south-1 --profile treefleet` (or refresh SSO). Retry `up.ps1` / `tunnel.ps1`. |
| Tunnel closed | Restart `.\deploy\aws\tunnel.ps1`. Browse http://127.0.0.1:8765 again (not the public DNS). |
| UI not active | Via SSM shell: `systemctl is-active treefleet-ui` and `curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/`. If inactive: `sudo systemctl restart treefleet-ui`, then re-check. |
| `GH_TOKEN` read-only or expired | Rotate with `.\deploy\aws\put-secrets.ps1`, then on the box `sudo systemctl restart treefleet-ui` (re-runs `render-env.sh` + `setup-host.sh`). Prove write with smoke’s dry-run or `git push --dry-run` — not `ls-remote` alone. |
| Agent run slow or hung | `cursor-agent --print` sometimes finishes the edit but fails to exit (outer timeout **rc=124**). TreeFleet trusts the worktree/git result, not that exit code alone. Wait for board/task state; check the worktree if needed. |
| Instance will not stop | `aws ec2 stop-instances --instance-ids i-0db26e98e83a8cca2 --profile treefleet --region ap-south-1`, then `describe-instances` until state is `stopped`. |

## Cost and safety

- **Stop after every session** (`.\deploy\aws\down.ps1`). EBS still costs a little while stopped; running hours dominate.
- AWS Budgets alert (**$15**/month) emails the configured friend account from the stack parameters.
- **WARNING:** Do **not** run `aws cloudformation deploy` (or execute a change set) on stack **`treefleet`**. The live security group was changed out-of-band to all-egress, and the AMI parameter has drifted via the SSM dynamic resolve — an update can **replace the instance**. Pin the AMI to the running image before any future redeploy. See [`docs/DEPLOY-AWS.md`](DEPLOY-AWS.md) D10 notes.

## Token expiry

The fine-grained GitHub token for `pr-sentinel-demo` (Contents RW, Pull requests RW) expires **30 days after creation**. The writable token used for the successful AWS proof was created around **2026-11-23**; rotate with `put-secrets.ps1` and restart `treefleet-ui` **before** an evaluation if that date is more than ~30 days ago (or sooner if GitHub shows an earlier expiry).
