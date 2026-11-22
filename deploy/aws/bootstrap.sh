#!/usr/bin/env bash
# Idempotent first-boot / re-bootstrap for TreeFleet on Ubuntu 24.04.
# Never echoes secret values. Logs to /var/log/treefleet-bootstrap.log.
#
# Cloud-init user-data often runs as root with HOME unset; set defaults BEFORE set -u.
export HOME="${HOME:-/root}"
export PATH="/root/.local/bin:/home/treefleet/.local/bin:/usr/local/bin:/snap/bin:/usr/bin:/bin${PATH:+:$PATH}"
export DEBIAN_FRONTEND="${DEBIAN_FRONTEND:-noninteractive}"

set -euo pipefail

LOG=/var/log/treefleet-bootstrap.log
exec > >(tee -a "$LOG") 2>&1

# Re-runs (SSM) may omit user-data exports; recover from existing checkouts when present.
if [[ -z "${TREEFLEET_REPO_URL:-}" && -d /opt/treefleet/.git ]]; then
  TREEFLEET_REPO_URL="$(git -C /opt/treefleet remote get-url origin)"
fi
if [[ -z "${TARGET_REPO_URL:-}" && -d /var/lib/treefleet/repo/.git ]]; then
  TARGET_REPO_URL="$(git -C /var/lib/treefleet/repo remote get-url origin)"
fi
if [[ -z "${AWS_DEFAULT_REGION:-}" ]]; then
  _tok="$(curl -fsS -m 2 -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 60" 2>/dev/null || true)"
  if [[ -n "${_tok}" ]]; then
    AWS_DEFAULT_REGION="$(curl -fsS -m 2 -H "X-aws-ec2-metadata-token: ${_tok}" \
      http://169.254.169.254/latest/meta-data/placement/region 2>/dev/null || true)"
  fi
  unset _tok
fi
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-}"

: "${TREEFLEET_REPO_URL:?TREEFLEET_REPO_URL required}"
: "${TREEFLEET_REPO_REF:=main}"
: "${TARGET_REPO_URL:?TARGET_REPO_URL required}"
: "${PARAMETER_PREFIX:=/treefleet}"
: "${IDLE_MINUTES:=45}"
: "${MAX_UPTIME_HOURS:=12}"

PREFIX="${PARAMETER_PREFIX%/}"

echo "==> apt packages"
apt-get update -y
apt-get install -y \
  git curl ca-certificates build-essential \
  python3.12 python3.12-venv python3-pip \
  unzip jq

echo "==> GitHub CLI"
if ! command -v gh >/dev/null 2>&1; then
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg status=none
  chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list
  apt-get update -y
  apt-get install -y gh
fi

echo "==> AWS CLI v2 (snap)"
if ! command -v aws >/dev/null 2>&1; then
  snap install aws-cli --classic
fi
# snap installs land on /snap/bin; keep PATH explicit for later aws calls
export PATH="/snap/bin:${PATH}"

echo "==> uv"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # shellcheck disable=SC1091
  if [[ -f "${HOME}/.local/bin/env" ]]; then
    # shellcheck disable=SC1091
    source "${HOME}/.local/bin/env"
  fi
fi
export PATH="${HOME}/.local/bin:/root/.local/bin:${PATH}"

echo "==> user and directories"
id treefleet >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash treefleet
install -d -o treefleet -g treefleet -m 0755 \
  /opt/treefleet \
  /var/lib/treefleet \
  /var/lib/treefleet/repo \
  /var/lib/treefleet/runs \
  /var/lib/treefleet/wt \
  /etc/treefleet

echo "==> TreeFleet source at /opt/treefleet"
if [[ ! -d /opt/treefleet/.git ]]; then
  git clone --depth 1 --branch "$TREEFLEET_REPO_REF" "$TREEFLEET_REPO_URL" /opt/treefleet
else
  git -C /opt/treefleet fetch --depth 1 origin "$TREEFLEET_REPO_REF" || \
    git -C /opt/treefleet fetch --depth 1 origin
  git -C /opt/treefleet checkout -B "$TREEFLEET_REPO_REF" "FETCH_HEAD" 2>/dev/null || \
    git -C /opt/treefleet checkout -B "$TREEFLEET_REPO_REF" "origin/${TREEFLEET_REPO_REF}"
fi
chown -R treefleet:treefleet /opt/treefleet

echo "==> target repo at /var/lib/treefleet/repo"
# Optional GH_TOKEN from SSM (never logged). Anonymous clone if absent.
GH_TOKEN=""
if command -v aws >/dev/null 2>&1 && [[ -n "${AWS_DEFAULT_REGION}" ]]; then
  GH_TOKEN="$(aws ssm get-parameter --name "${PREFIX}/GH_TOKEN" --with-decryption --query Parameter.Value --output text 2>/dev/null || true)"
  if [[ "$GH_TOKEN" == "None" ]]; then GH_TOKEN=""; fi
fi
clone_url="$TARGET_REPO_URL"
if [[ -n "$GH_TOKEN" ]]; then
  # Rewrite https://github.com/org/repo → https://x-access-token:REDACTED@github.com/org/repo
  clone_url="$(python3 - "$TARGET_REPO_URL" "$GH_TOKEN" <<'PY'
import sys
from urllib.parse import urlsplit, urlunsplit
url, token = sys.argv[1], sys.argv[2]
parts = urlsplit(url)
netloc = f"x-access-token:{token}@{parts.hostname}"
if parts.port:
    netloc += f":{parts.port}"
print(urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment)))
PY
)"
fi
if [[ ! -d /var/lib/treefleet/repo/.git ]]; then
  sudo -u treefleet git clone --depth 1 "$clone_url" /var/lib/treefleet/repo
else
  sudo -u treefleet git -C /var/lib/treefleet/repo remote set-url origin "$TARGET_REPO_URL" || true
  sudo -u treefleet git -C /var/lib/treefleet/repo fetch --depth 1 origin || true
fi
unset GH_TOKEN clone_url
chown -R treefleet:treefleet /var/lib/treefleet/repo

echo "==> Python venvs (matches AGENTS.md / onboarding)"
cd /opt/treefleet
# Install uv for treefleet if missing (root's uv is not on that user's PATH).
sudo -u treefleet -H bash -lc '
  set -euo pipefail
  export HOME="${HOME:-/home/treefleet}"
  export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
  if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # shellcheck disable=SC1091
    if [[ -f "$HOME/.local/bin/env" ]]; then source "$HOME/.local/bin/env"; fi
    export PATH="$HOME/.local/bin:$PATH"
  fi
  cd /opt/treefleet
  uv venv .venv-orch
  uv pip install -p .venv-orch -e ".[orchestrator,dev]"
  uv venv .venv-worker
  uv pip install -p .venv-worker -e ".[worker]"
'

echo "==> cursor-agent as treefleet (never root)"
# Supply-chain note: installer URL is https://cursor.com/install — pin/review before production.
sudo -u treefleet -H bash -lc '
  set -euo pipefail
  export HOME="${HOME:-/home/treefleet}"
  export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
  if ! command -v cursor-agent >/dev/null 2>&1 && ! command -v agent >/dev/null 2>&1; then
    curl -fsS https://cursor.com/install | bash
  fi
  command -v cursor-agent >/dev/null 2>&1 || command -v agent >/dev/null 2>&1
'

echo "==> /etc/treefleet/treefleet.toml"
# Start from repo example; force durable worktree root.
if [[ -f /opt/treefleet/treefleet.toml ]]; then
  cp /opt/treefleet/treefleet.toml /etc/treefleet/treefleet.toml
elif [[ -f /opt/treefleet/treefleet.toml.example ]]; then
  cp /opt/treefleet/treefleet.toml.example /etc/treefleet/treefleet.toml
else
  printf '[paths]\nworktree_other = "/var/lib/treefleet/wt"\n' > /etc/treefleet/treefleet.toml
fi
python3 - <<'PY'
from pathlib import Path
path = Path("/etc/treefleet/treefleet.toml")
text = path.read_text(encoding="utf-8") if path.is_file() else ""
if "worktree_other" in text:
    import re
    text = re.sub(
        r'(?m)^(worktree_other\s*=\s*).*$',
        r'\1"/var/lib/treefleet/wt"',
        text,
    )
else:
    if "[paths]" not in text:
        text += "\n[paths]\n"
    text += 'worktree_other = "/var/lib/treefleet/wt"\n'
path.write_text(text, encoding="utf-8")
PY
chown treefleet:treefleet /etc/treefleet/treefleet.toml
chmod 0644 /etc/treefleet/treefleet.toml

echo "==> install helper scripts and systemd units"
install -d -m 0755 /usr/local/lib/treefleet
install -o root -g root -m 0755 /opt/treefleet/deploy/aws/render-env.sh /usr/local/lib/treefleet/render-env.sh
install -o root -g root -m 0755 /opt/treefleet/deploy/aws/idle-watch.sh /usr/local/lib/treefleet/idle-watch.sh
install -o root -g root -m 0755 /opt/treefleet/deploy/aws/idle_decide.py /usr/local/lib/treefleet/idle_decide.py
install -o root -g root -m 0755 /opt/treefleet/deploy/aws/smoke.sh /usr/local/bin/treefleet-smoke

install -o root -g root -m 0644 /opt/treefleet/deploy/aws/treefleet-ui.service /etc/systemd/system/treefleet-ui.service
install -o root -g root -m 0644 /opt/treefleet/deploy/aws/treefleet-idle.service /etc/systemd/system/treefleet-idle.service
install -o root -g root -m 0644 /opt/treefleet/deploy/aws/treefleet-idle.timer /etc/systemd/system/treefleet-idle.timer

# Persist watchdog knobs for the idle unit
cat > /etc/treefleet/idle.env <<EOF
IDLE_MINUTES=${IDLE_MINUTES}
MAX_UPTIME_HOURS=${MAX_UPTIME_HOURS}
PARAMETER_PREFIX=${PREFIX}
EOF
chmod 0644 /etc/treefleet/idle.env

# Placeholder env so systemd can start before first render
if [[ ! -f /etc/treefleet/env ]]; then
  umask 077
  touch /etc/treefleet/env
  chown treefleet:treefleet /etc/treefleet/env
  chmod 0600 /etc/treefleet/env
fi

systemctl daemon-reload
systemctl enable treefleet-ui.service treefleet-idle.timer
systemctl restart treefleet-ui.service || systemctl start treefleet-ui.service
systemctl start treefleet-idle.timer

echo "==> bootstrap complete"
