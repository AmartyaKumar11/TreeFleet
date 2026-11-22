#!/usr/bin/env bash
# Pull SSM SecureString parameters under PARAMETER_PREFIX into /etc/treefleet/env (0600).
# Never logs parameter values.
#
# Live vs stub: when DEEPSEEK_API_KEY and CURSOR_API_KEY are both present from SSM,
# export them as-is and do not set TREEFLEET_STUB_FLEET. Placeholders / stub mode
# apply only when those required keys are absent. Never overwrite a real value.
set -euo pipefail

PREFIX="${PARAMETER_PREFIX:-/treefleet}"
PREFIX="${PREFIX%/}"
OUT=/etc/treefleet/env
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

if [[ -z "${AWS_DEFAULT_REGION:-}" ]]; then
  _tok="$(curl -fsS -m 2 -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 60" 2>/dev/null || true)"
  if [[ -n "$_tok" ]]; then
    AWS_DEFAULT_REGION="$(curl -fsS -m 2 -H "X-aws-ec2-metadata-token: ${_tok}" \
      http://169.254.169.254/latest/meta-data/placement/region 2>/dev/null || true)"
  fi
  unset _tok
fi
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-}"
if [[ -n "${AWS_DEFAULT_REGION}" ]]; then
  export AWS_REGION="${AWS_DEFAULT_REGION}"
fi

{
  echo "# rendered by render-env.sh — do not commit"
  echo "TREEFLEET_CONFIG=/etc/treefleet/treefleet.toml"
  echo "PARAMETER_PREFIX=${PREFIX}"
  if [[ -n "${AWS_DEFAULT_REGION:-}" ]]; then
    echo "AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION}"
    echo "AWS_REGION=${AWS_DEFAULT_REGION}"
  fi
  echo 'PATH=/home/treefleet/.local/bin:/usr/local/bin:/usr/bin:/bin'
} >"$TMP"

have_deepseek=0
have_cursor=0
have_sandbox=0

# Fetch each known key by absolute name. Path listing alone was flaky under
# systemd ExecStartPre (partial key sets -> accidental stub mode).
KNOWN_KEYS=(
  DEEPSEEK_API_KEY
  TYPESAFE_API_KEY
  CURSOR_API_KEY
  LANGFUSE_PUBLIC_KEY
  LANGFUSE_SECRET_KEY
  LANGFUSE_BASE_URL
  GH_TOKEN
  GITHUB_TOKEN
  TREEFLEET_CURSOR_SANDBOX
)

if command -v aws >/dev/null 2>&1; then
  for key in "${KNOWN_KEYS[@]}"; do
    name="${PREFIX}/${key}"
    val="$(aws ssm get-parameter --name "$name" --with-decryption \
      --query Parameter.Value --output text 2>/dev/null || true)"
    if [[ -n "$val" && "$val" != "None" ]]; then
      printf "%s=%s\n" "$key" "$val" >>"$TMP"
      case "$key" in
        DEEPSEEK_API_KEY) have_deepseek=1 ;;
        CURSOR_API_KEY) have_cursor=1 ;;
        TREEFLEET_CURSOR_SANDBOX) have_sandbox=1 ;;
      esac
    fi
  done
fi

# Hosting-only fallbacks when put-secrets was not run. Never override real SSM values.
if (( have_deepseek == 0 )); then
  echo "DEEPSEEK_API_KEY=treefleet-hosting-placeholder" >>"$TMP"
fi
if (( have_cursor == 0 )); then
  echo "TREEFLEET_STUB_FLEET=1" >>"$TMP"
fi
# Hosted Ubuntu: default sandbox off unless SSM sets TREEFLEET_CURSOR_SANDBOX.
if (( have_sandbox == 0 )); then
  echo "TREEFLEET_CURSOR_SANDBOX=disabled" >>"$TMP"
fi

install -o treefleet -g treefleet -m 0600 "$TMP" "$OUT"
