#!/usr/bin/env bash
# Pull SSM SecureString parameters under PARAMETER_PREFIX into /etc/treefleet/env (0600).
# Never logs parameter values.
set -euo pipefail

PREFIX="${PARAMETER_PREFIX:-/treefleet}"
PREFIX="${PREFIX%/}"
OUT=/etc/treefleet/env
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

if [[ -z "${AWS_DEFAULT_REGION:-}" ]]; then
  # IMDSv2 (required on this instance)
  _tok="$(curl -fsS -m 2 -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 60" 2>/dev/null || true)"
  if [[ -n "$_tok" ]]; then
    AWS_DEFAULT_REGION="$(curl -fsS -m 2 -H "X-aws-ec2-metadata-token: ${_tok}" \
      http://169.254.169.254/latest/meta-data/placement/region 2>/dev/null || true)"
  fi
  unset _tok
fi

{
  echo "# rendered by render-env.sh — do not commit"
  echo "TREEFLEET_CONFIG=/etc/treefleet/treefleet.toml"
  echo "PARAMETER_PREFIX=${PREFIX}"
  if [[ -n "${AWS_DEFAULT_REGION:-}" ]]; then
    echo "AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION}"
    echo "AWS_REGION=${AWS_DEFAULT_REGION}"
  fi
  # Ensure Cursor CLI is findable for the treefleet user
  echo 'PATH=/home/treefleet/.local/bin:/usr/local/bin:/usr/bin:/bin'
} >"$TMP"

have_deepseek=0
have_cursor=0

if command -v aws >/dev/null 2>&1; then
  # Names only in the path listing; values fetched one-by-one without printing.
  mapfile -t NAMES < <(aws ssm get-parameters-by-path \
    --path "$PREFIX" \
    --recursive \
    --query 'Parameters[].Name' \
    --output text 2>/dev/null | tr '\t' '\n' | sed '/^$/d' || true)
  for name in "${NAMES[@]:-}"; do
    [[ -z "$name" ]] && continue
    key="${name##*/}"
    # Only allow known env-style keys
    case "$key" in
      DEEPSEEK_API_KEY|TYPESAFE_API_KEY|CURSOR_API_KEY|LANGFUSE_PUBLIC_KEY|LANGFUSE_SECRET_KEY|LANGFUSE_BASE_URL|GH_TOKEN|GITHUB_TOKEN|TREEFLEET_CURSOR_SANDBOX)
        val="$(aws ssm get-parameter --name "$name" --with-decryption --query Parameter.Value --output text 2>/dev/null || true)"
        if [[ -n "$val" && "$val" != "None" ]]; then
          printf "%s=%s\n" "$key" "$val" >>"$TMP"
          if [[ "$key" == "DEEPSEEK_API_KEY" ]]; then have_deepseek=1; fi
          if [[ "$key" == "CURSOR_API_KEY" ]]; then have_cursor=1; fi
        fi
        ;;
      *)
        ;;
    esac
  done
fi

# Hosting proof without put-secrets: load_settings() requires DEEPSEEK_API_KEY.
# Placeholder is not a real credential; stub fleet when Cursor key is also absent.
if (( have_deepseek == 0 )); then
  echo "DEEPSEEK_API_KEY=treefleet-hosting-placeholder" >>"$TMP"
fi
if (( have_cursor == 0 )); then
  echo "TREEFLEET_STUB_FLEET=1" >>"$TMP"
fi

install -o treefleet -g treefleet -m 0600 "$TMP" "$OUT"
