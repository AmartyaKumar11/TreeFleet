#!/usr/bin/env bash
# On-box smoke checks. Never prints the bearer token value.
set -euo pipefail

RUN_TESTS=0
if [[ "${1:-}" == "--tests" ]]; then
  RUN_TESTS=1
fi

fail=0
ok() { echo "OK  $*"; }
bad() { echo "FAIL $*"; fail=1; }

systemctl is-active --quiet treefleet-ui.service && ok "treefleet-ui.service active" || bad "treefleet-ui.service not active"

html="$(curl -fsS --max-time 10 http://127.0.0.1:8765/ || true)"
if [[ -n "$html" ]] && grep -q 'name="tf-token"' <<<"$html"; then
  ok "GET / returns 200-class body with tf-token meta"
else
  bad "GET / missing or no tf-token meta"
fi

# Extract token without printing it
token="$(python3 - <<'PY' "$html"
import re, sys
html = sys.argv[1]
m = re.search(r'name="tf-token"\s+content="([^"]+)"', html)
print(m.group(1) if m else "")
PY
)"

for path in /static/css/tokens.css /static/js/board.js /static/js/transcript.js; do
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:8765${path}" || echo 000)"
  if [[ "$code" == "200" ]]; then
    ok "GET ${path} -> 200"
  else
    bad "GET ${path} -> ${code}"
  fi
done

if [[ -n "$token" ]]; then
  board_code="$(curl -sS -o /tmp/treefleet-board.json -w '%{http_code}' --max-time 10 \
    -H "Authorization: Bearer ${token}" http://127.0.0.1:8765/api/board || echo 000)"
  if [[ "$board_code" == "200" ]] && python3 -c 'import json,sys; json.load(open("/tmp/treefleet-board.json"))' 2>/dev/null; then
    ok "/api/board JSON 200"
  else
    bad "/api/board -> ${board_code}"
  fi
  rm -f /tmp/treefleet-board.json
else
  bad "could not parse tf-token from HTML"
fi
unset token html

if command -v cursor-agent >/dev/null 2>&1; then
  cursor-agent --version >/dev/null && ok "cursor-agent --version" || bad "cursor-agent --version failed"
elif command -v agent >/dev/null 2>&1; then
  agent --version >/dev/null && ok "agent --version" || bad "agent --version failed"
else
  bad "neither cursor-agent nor agent on PATH"
fi

gh --version >/dev/null && ok "gh --version" || bad "gh missing"
git --version >/dev/null && ok "git version" || bad "git missing"

avail_kb="$(df -Pk /var/lib/treefleet | awk 'NR==2{print $4}')"
if [[ "${avail_kb:-0}" -gt 1048576 ]]; then
  ok "disk free /var/lib/treefleet > 1 GiB (${avail_kb} KiB)"
else
  bad "disk free low: ${avail_kb:-0} KiB"
fi

if (( RUN_TESTS == 1 )); then
  cd /opt/treefleet
  set +e
  out="$(sudo -u treefleet -H bash -lc 'cd /opt/treefleet && .venv-orch/bin/python -m pytest -q -m "not ui and not live and not packaging"' 2>&1)"
  rc=$?
  set -e
  echo "$out" | tail -n 5
  if [[ $rc -eq 0 ]]; then
    ok "pytest non-UI subset passed"
  else
    bad "pytest non-UI subset failed (exit ${rc})"
  fi
fi

exit "$fail"
