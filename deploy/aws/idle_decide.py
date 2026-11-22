"""Pure idle-watchdog decision helper (tested offline; used by idle-watch.sh via python -c)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IdleInputs:
    idle_minutes: int
    max_uptime_hours: int
    minutes_since_active: float
    uptime_minutes: float
    has_ui_connection: bool
    has_agent_process: bool
    events_recent: bool
    boot_grace: bool  # uptime under 20 minutes


def decide(inp: IdleInputs) -> str:
    """Return one of: keep, poweroff_idle, poweroff_max_uptime."""
    if inp.has_ui_connection or inp.has_agent_process or inp.events_recent or inp.boot_grace:
        return "keep"
    if inp.uptime_minutes >= inp.max_uptime_hours * 60 and not inp.has_ui_connection:
        return "poweroff_max_uptime"
    if inp.minutes_since_active >= inp.idle_minutes:
        return "poweroff_idle"
    return "keep"
