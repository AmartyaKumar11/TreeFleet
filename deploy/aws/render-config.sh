#!/usr/bin/env bash
# Patch /etc/treefleet/treefleet.toml for the EC2 layout.
# Relative orchestrator_venv / worker_venv join under the toml directory
# (config.py resolve_venv_root); with TREEFLEET_CONFIG=/etc/treefleet/treefleet.toml
# that wrongly becomes /etc/treefleet/.venv-*. Absolute paths under /opt/treefleet win.
set -euo pipefail

OPT_ROOT="${TREEFLEET_OPT_ROOT:-/opt/treefleet}"
WT_ROOT="${TREEFLEET_WT_ROOT:-/var/lib/treefleet/wt}"
OUT="${TREEFLEET_CONFIG:-/etc/treefleet/treefleet.toml}"

install -d -m 0755 "$(dirname "$OUT")"

if [[ -f "${OPT_ROOT}/treefleet.toml" ]]; then
  cp "${OPT_ROOT}/treefleet.toml" "$OUT"
elif [[ -f "${OPT_ROOT}/treefleet.toml.example" ]]; then
  cp "${OPT_ROOT}/treefleet.toml.example" "$OUT"
elif [[ ! -f "$OUT" ]]; then
  printf '[paths]\nworktree_other = "%s"\n' "$WT_ROOT" >"$OUT"
fi

python3 - "$OUT" "$OPT_ROOT" "$WT_ROOT" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
opt = sys.argv[2].rstrip("/")
wt = sys.argv[3]
text = path.read_text(encoding="utf-8") if path.is_file() else ""

if "[paths]" not in text:
    text += "\n[paths]\n"

def set_key(body: str, key: str, value: str) -> str:
    line = f'{key} = "{value}"'
    pattern = rf"(?m)^({re.escape(key)}\s*=\s*).*$"
    if re.search(pattern, body):
        return re.sub(pattern, line, body)
    # Insert after [paths]
    return re.sub(r"(?m)^(\[paths\]\s*)$", rf"\1\n{line}", body, count=1)

text = set_key(text, "worktree_other", wt)
text = set_key(text, "orchestrator_venv", f"{opt}/.venv-orch")
text = set_key(text, "worker_venv", f"{opt}/.venv-worker")
path.write_text(text, encoding="utf-8")
print(f"wrote {path}")
PY

chown treefleet:treefleet "$OUT" 2>/dev/null || true
chmod 0644 "$OUT"
