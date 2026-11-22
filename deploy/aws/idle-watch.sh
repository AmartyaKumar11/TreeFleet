#!/usr/bin/env bash
# Idle / max-uptime watchdog. Logs decisions to journald; never prints secrets.
set -euo pipefail

# shellcheck disable=SC1091
[[ -f /etc/treefleet/idle.env ]] && source /etc/treefleet/idle.env

IDLE_MINUTES="${IDLE_MINUTES:-45}"
MAX_UPTIME_HOURS="${MAX_UPTIME_HOURS:-12}"
STATE_DIR=/var/lib/treefleet
STAMP="${STATE_DIR}/.last-active"
mkdir -p "$STATE_DIR"

now="$(date +%s)"
uptime_s="$(awk '{print int($1)}' /proc/uptime)"
uptime_min=$((uptime_s / 60))

has_ui=0
if ss -Htn state established '( sport = :8765 )' 2>/dev/null | grep -q .; then
  has_ui=1
fi

has_agent=0
if pgrep -u treefleet -f 'cursor-agent|agent --|treefleet.worker.main' >/dev/null 2>&1; then
  has_agent=1
fi

events_recent=0
if find "$STATE_DIR/runs" -type f -name 'events.jsonl' -mmin -10 2>/dev/null | grep -q .; then
  events_recent=1
fi

boot_grace=0
if (( uptime_min < 20 )); then
  boot_grace=1
fi

# Refresh last-active when any activity signal is present
if (( has_ui == 1 || has_agent == 1 || events_recent == 1 || boot_grace == 1 )); then
  echo "$now" >"$STAMP"
fi

if [[ ! -f "$STAMP" ]]; then
  echo "$now" >"$STAMP"
fi
last="$(cat "$STAMP")"
minutes_since=$(( (now - last) / 60 ))

decision="$(
  IDLE_MINUTES="$IDLE_MINUTES" MAX_UPTIME_HOURS="$MAX_UPTIME_HOURS" \
  MINUTES_SINCE="$minutes_since" UPTIME_MIN="$uptime_min" \
  HAS_UI="$has_ui" HAS_AGENT="$has_agent" EVENTS="$events_recent" GRACE="$boot_grace" \
  python3 - <<'PY'
import os
from pathlib import Path
import sys
sys.path.insert(0, "/opt/treefleet/deploy/aws")
from idle_decide import IdleInputs, decide  # noqa: E402

inp = IdleInputs(
    idle_minutes=int(os.environ["IDLE_MINUTES"]),
    max_uptime_hours=int(os.environ["MAX_UPTIME_HOURS"]),
    minutes_since_active=float(os.environ["MINUTES_SINCE"]),
    uptime_minutes=float(os.environ["UPTIME_MIN"]),
    has_ui_connection=os.environ["HAS_UI"] == "1",
    has_agent_process=os.environ["HAS_AGENT"] == "1",
    events_recent=os.environ["EVENTS"] == "1",
    boot_grace=os.environ["GRACE"] == "1",
)
print(decide(inp))
PY
)"

logger -t treefleet-idle "decision=${decision} idle_min=${IDLE_MINUTES} since=${minutes_since} uptime_min=${uptime_min} ui=${has_ui} agent=${has_agent} events=${events_recent} grace=${boot_grace}"

case "$decision" in
  keep)
    exit 0
    ;;
  poweroff_idle|poweroff_max_uptime)
    logger -t treefleet-idle "powering off (${decision})"
    /sbin/systemctl poweroff
    ;;
  *)
    logger -t treefleet-idle "unknown decision=${decision}; keeping"
    exit 0
    ;;
esac
