#!/usr/bin/env bash
# Patch /etc/treefleet/treefleet.toml for the EC2 layout.
# Relative orchestrator_venv / worker_venv join under the toml directory
# (config.py resolve_venv_root); with TREEFLEET_CONFIG=/etc/treefleet/treefleet.toml
# that wrongly becomes /etc/treefleet/.venv-*. Absolute paths under /opt/treefleet win.
set -euo pipefail

OPT_ROOT="${TREEFLEET_OPT_ROOT:-/opt/treefleet}"
WT_ROOT="${TREEFLEET_WT_ROOT:-/var/lib/treefleet/wt}"
OUT="${TREEFLEET_CONFIG:-/etc/treefleet/treefleet.toml}"
PR_REPO="${TREEFLEET_PR_REPO:-AmartyaKumar11/pr-sentinel-demo}"
PR_BASE="${TREEFLEET_PR_BASE:-main}"

install -d -m 0755 "$(dirname "$OUT")"

if [[ -f "${OPT_ROOT}/treefleet.toml" ]]; then
  cp "${OPT_ROOT}/treefleet.toml" "$OUT"
elif [[ -f "${OPT_ROOT}/treefleet.toml.example" ]]; then
  cp "${OPT_ROOT}/treefleet.toml.example" "$OUT"
elif [[ ! -f "$OUT" ]]; then
  printf '[paths]\nworktree_other = "%s"\n' "$WT_ROOT" >"$OUT"
fi

python3 - "$OUT" "$OPT_ROOT" "$WT_ROOT" "$PR_REPO" "$PR_BASE" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
opt = sys.argv[2].rstrip("/")
wt = sys.argv[3]
pr_repo = sys.argv[4]
pr_base = sys.argv[5]
text = path.read_text(encoding="utf-8") if path.is_file() else ""

def ensure_section(body: str, name: str) -> str:
    if f"[{name}]" not in body:
        return body + f"\n[{name}]\n"
    return body

def set_in_section(body: str, section: str, key: str, raw_line: str) -> str:
    pattern = rf"(?m)^({re.escape(key)}\s*=\s*).*$"
    # Prefer replacing an existing key anywhere (paths/pr keys are unique in our toml).
    if re.search(pattern, body):
        return re.sub(pattern, raw_line, body)
    return re.sub(rf"(?m)^(\[{re.escape(section)}\]\s*)$", rf"\1\n{raw_line}", body, count=1)

text = ensure_section(text, "paths")
text = set_in_section(text, "paths", "worktree_other", f'worktree_other = "{wt}"')
text = set_in_section(text, "paths", "orchestrator_venv", f'orchestrator_venv = "{opt}/.venv-orch"')
text = set_in_section(text, "paths", "worker_venv", f'worker_venv = "{opt}/.venv-worker"')

# Live hosting: enable PR stage against the demo target (never auto_merge).
text = ensure_section(text, "pr")
text = set_in_section(text, "pr", "enabled", "enabled = true")
text = set_in_section(text, "pr", "repo", f'repo = "{pr_repo}"')
text = set_in_section(text, "pr", "base", f'base = "{pr_base}"')
text = set_in_section(text, "pr", "auto_merge", "auto_merge = false")

path.write_text(text, encoding="utf-8")
print(f"wrote {path}")
PY

chown treefleet:treefleet "$OUT" 2>/dev/null || true
chmod 0644 "$OUT"
