#!/usr/bin/env bash
# Idempotent host setup for the treefleet service user (git identity + gh HTTPS helper).
# Never prints secret values. GH_TOKEN stays in /etc/treefleet/env (0600) only.
set -euo pipefail

OPT_ROOT="${TREEFLEET_OPT_ROOT:-/opt/treefleet}"
REPO="${TREEFLEET_TARGET_REPO:-/var/lib/treefleet/repo}"
WT_ROOT="${TREEFLEET_WT_ROOT:-/var/lib/treefleet/wt}"
RUNS_ROOT="${TREEFLEET_RUNS_ROOT:-/var/lib/treefleet/runs}"
ENV_FILE="${TREEFLEET_ENV_FILE:-/etc/treefleet/env}"
GIT_NAME="${TREEFLEET_GIT_NAME:-TreeFleet Bot}"
GIT_EMAIL="${TREEFLEET_GIT_EMAIL:-treefleet-bot@users.noreply.github.com}"

as_treefleet() {
  sudo -u treefleet -H env HOME=/home/treefleet \
    PATH="/home/treefleet/.local/bin:/usr/local/bin:/usr/bin:/bin" \
    "$@"
}

install -d -o treefleet -g treefleet -m 0755 \
  /var/lib/treefleet "$REPO" "$WT_ROOT" "$RUNS_ROOT" /home/treefleet/.local/bin

as_treefleet git config --global user.name "$GIT_NAME"
as_treefleet git config --global user.email "$GIT_EMAIL"
as_treefleet git config --global --replace-all safe.directory '*'

if [[ -d "$REPO/.git" ]]; then
  as_treefleet git -C "$REPO" config --local user.name "$GIT_NAME"
  as_treefleet git -C "$REPO" config --local user.email "$GIT_EMAIL"
fi

# Pull GH_TOKEN into this process only (never echo). Prefer GH_TOKEN, else GITHUB_TOKEN.
_gh=""
if [[ -f "$ENV_FILE" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      GH_TOKEN=*) _gh="${line#GH_TOKEN=}" ;;
      GITHUB_TOKEN=*)
        if [[ -z "$_gh" ]]; then _gh="${line#GITHUB_TOKEN=}"; fi
        ;;
    esac
  done <"$ENV_FILE"
fi

if command -v gh >/dev/null 2>&1; then
  if [[ -n "$_gh" ]]; then
    # Configures git to use gh as an HTTPS credential helper (token stays in env, not a new file).
    as_treefleet env GH_TOKEN="$_gh" GITHUB_TOKEN="$_gh" gh auth setup-git
  else
    echo "setup-host: GH_TOKEN absent; skipping gh auth setup-git" >&2
  fi
else
  echo "setup-host: gh not installed; skipping" >&2
fi
unset _gh

chown -R treefleet:treefleet /var/lib/treefleet /home/treefleet
chown -R treefleet:treefleet "$OPT_ROOT" || true

echo "setup-host: git identity and permissions ok"
