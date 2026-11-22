#!/usr/bin/env bash
# Idempotent first-boot / re-bootstrap for TreeFleet on Ubuntu 24.04.
# Never echoes secret values. Logs to /var/log/treefleet-bootstrap.log.
#
# Design: root installs packages and systemd units; repo / venv / cursor-agent
# work runs as user treefleet so ownership stays consistent across re-runs.
#
# Cloud-init user-data often runs as root with HOME unset; set defaults BEFORE set -u.
export HOME="${HOME:-/root}"
export PATH="/root/.local/bin:/home/treefleet/.local/bin:/usr/local/bin:/snap/bin:/usr/bin:/bin${PATH:+:$PATH}"
export DEBIAN_FRONTEND="${DEBIAN_FRONTEND:-noninteractive}"

set -euo pipefail

LOG=/var/log/treefleet-bootstrap.log
exec > >(tee -a "$LOG") 2>&1

as_treefleet() {
  # Always give the service user a real HOME and PATH (sudo -H alone is not enough under set -u).
  sudo -u treefleet -H env HOME=/home/treefleet \
    PATH="/home/treefleet/.local/bin:/usr/local/bin:/usr/bin:/bin" \
    "$@"
}

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

# Last-resort defaults match the CloudFormation template (public repos).
: "${TREEFLEET_REPO_URL:=https://github.com/AmartyaKumar11/TreeFleet.git}"
: "${TREEFLEET_REPO_REF:=main}"
: "${TARGET_REPO_URL:=https://github.com/AmartyaKumar11/pr-sentinel-demo}"
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
export PATH="/snap/bin:${PATH}"

echo "==> user and directories"
id treefleet >/dev/null 2>&1 || useradd --system --create-home --shell /bin/bash treefleet
install -d -o treefleet -g treefleet -m 0755 \
  /opt/treefleet \
  /var/lib/treefleet \
  /var/lib/treefleet/repo \
  /var/lib/treefleet/runs \
  /var/lib/treefleet/wt \
  /etc/treefleet \
  /home/treefleet/.local/bin

# Prefer URLs from existing checkouts when user-data env is missing (re-run).
if [[ -d /opt/treefleet/.git ]]; then
  _u="$(as_treefleet git -C /opt/treefleet remote get-url origin 2>/dev/null || true)"
  if [[ -n "${_u}" ]]; then TREEFLEET_REPO_URL="${_u}"; fi
  unset _u
fi
if [[ -d /var/lib/treefleet/repo/.git ]]; then
  _u="$(as_treefleet git -C /var/lib/treefleet/repo remote get-url origin 2>/dev/null || true)"
  if [[ -n "${_u}" ]]; then TARGET_REPO_URL="${_u}"; fi
  unset _u
fi

echo "==> TreeFleet source at /opt/treefleet (as treefleet)"
# Ensure ownership before any git as treefleet (repairs root-owned leftovers).
chown -R treefleet:treefleet /opt/treefleet
if [[ ! -d /opt/treefleet/.git ]]; then
  # Clone into an empty owned dir: git refuses non-empty targets, so use a temp then move.
  as_treefleet bash -lc "
    set -euo pipefail
    tmp=\$(mktemp -d /tmp/treefleet-src.XXXXXX)
    git clone --depth 1 --branch '${TREEFLEET_REPO_REF}' '${TREEFLEET_REPO_URL}' \"\$tmp/src\"
    shopt -s dotglob
    mv \"\$tmp/src\"/* /opt/treefleet/
    rmdir \"\$tmp/src\" \"\$tmp\"
  "
else
  as_treefleet bash -lc "
    set -euo pipefail
    cd /opt/treefleet
    git remote set-url origin '${TREEFLEET_REPO_URL}' || true
    git fetch --depth 1 origin '${TREEFLEET_REPO_REF}' || git fetch --depth 1 origin
    git checkout -B '${TREEFLEET_REPO_REF}' FETCH_HEAD 2>/dev/null \
      || git checkout -B '${TREEFLEET_REPO_REF}' 'origin/${TREEFLEET_REPO_REF}'
  "
fi
chown -R treefleet:treefleet /opt/treefleet

echo "==> target repo at /var/lib/treefleet/repo (as treefleet)"
GH_TOKEN=""
if command -v aws >/dev/null 2>&1 && [[ -n "${AWS_DEFAULT_REGION}" ]]; then
  GH_TOKEN="$(aws ssm get-parameter --name "${PREFIX}/GH_TOKEN" --with-decryption --query Parameter.Value --output text 2>/dev/null || true)"
  if [[ "$GH_TOKEN" == "None" ]]; then GH_TOKEN=""; fi
fi
clone_url="$TARGET_REPO_URL"
if [[ -n "$GH_TOKEN" ]]; then
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
chown -R treefleet:treefleet /var/lib/treefleet/repo
if [[ ! -d /var/lib/treefleet/repo/.git ]]; then
  as_treefleet git clone --depth 1 "$clone_url" /var/lib/treefleet/repo
else
  as_treefleet git -C /var/lib/treefleet/repo remote set-url origin "$TARGET_REPO_URL" || true
  as_treefleet git -C /var/lib/treefleet/repo fetch --depth 1 origin || true
fi
unset GH_TOKEN clone_url
chown -R treefleet:treefleet /var/lib/treefleet/repo

echo "==> uv + Python venvs as treefleet (always --clear, always reinstall)"
# Intentional single quotes: expand HOME/PATH inside the treefleet user shell.
# shellcheck disable=SC2016
as_treefleet bash -lc '
  set -euo pipefail
  export HOME=/home/treefleet
  export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
  if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # shellcheck disable=SC1091
    if [[ -f "$HOME/.local/bin/env" ]]; then source "$HOME/.local/bin/env"; fi
    export PATH="$HOME/.local/bin:$PATH"
  fi
  command -v uv >/dev/null 2>&1
  cd /opt/treefleet
  uv venv --clear .venv-orch
  uv pip install -p .venv-orch -e ".[orchestrator,dev]"
  uv venv --clear .venv-worker
  uv pip install -p .venv-worker -e ".[worker]"
  test -x /opt/treefleet/.venv-orch/bin/treefleet
'

echo "==> cursor-agent as treefleet (never root)"
# Supply-chain note: installer URL is https://cursor.com/install — pin/review before production.
# shellcheck disable=SC2016
as_treefleet bash -lc '
  set -euo pipefail
  export HOME=/home/treefleet
  export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
  if ! command -v cursor-agent >/dev/null 2>&1 && ! command -v agent >/dev/null 2>&1; then
    curl -fsS https://cursor.com/install | bash
  fi
  command -v cursor-agent >/dev/null 2>&1 || command -v agent >/dev/null 2>&1
'

echo "==> /etc/treefleet/treefleet.toml"
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
install -o root -g root -m 0644 /opt/treefleet/deploy/aws/idle_decide.py /usr/local/lib/treefleet/idle_decide.py
install -o root -g root -m 0755 /opt/treefleet/deploy/aws/smoke.sh /usr/local/bin/treefleet-smoke

install -o root -g root -m 0644 /opt/treefleet/deploy/aws/treefleet-ui.service /etc/systemd/system/treefleet-ui.service
install -o root -g root -m 0644 /opt/treefleet/deploy/aws/treefleet-idle.service /etc/systemd/system/treefleet-idle.service
install -o root -g root -m 0644 /opt/treefleet/deploy/aws/treefleet-idle.timer /etc/systemd/system/treefleet-idle.timer

cat > /etc/treefleet/idle.env <<EOF
IDLE_MINUTES=${IDLE_MINUTES}
MAX_UPTIME_HOURS=${MAX_UPTIME_HOURS}
PARAMETER_PREFIX=${PREFIX}
EOF
chmod 0644 /etc/treefleet/idle.env

if [[ ! -f /etc/treefleet/env ]]; then
  umask 077
  touch /etc/treefleet/env
fi
chown treefleet:treefleet /etc/treefleet/env
chmod 0600 /etc/treefleet/env

echo "==> final ownership"
chown -R treefleet:treefleet /opt/treefleet /var/lib/treefleet /home/treefleet
chown treefleet:treefleet /etc/treefleet/treefleet.toml /etc/treefleet/env

systemctl daemon-reload
systemctl enable treefleet-ui.service treefleet-idle.timer
# Render env before first start so missing SSM secrets get hosting placeholders.
PARAMETER_PREFIX="${PREFIX}" AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-}" \
  /usr/local/lib/treefleet/render-env.sh
systemctl restart treefleet-ui.service || systemctl start treefleet-ui.service
systemctl start treefleet-idle.timer
# Give the unit a moment to leave "activating"
sleep 3
systemctl is-active treefleet-ui.service
systemctl is-active treefleet-idle.timer

echo "==> bootstrap complete"
