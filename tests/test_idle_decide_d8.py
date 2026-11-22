"""15.D8: table-driven idle watchdog decisions."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "deploy" / "aws" / "idle_decide.py"


def _load():
    import sys

    name = "treefleet_idle_decide_d8"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, MOD)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # dataclasses needs the module registered before @dataclass runs
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        (
            dict(
                idle_minutes=45,
                max_uptime_hours=12,
                minutes_since_active=60,
                uptime_minutes=30,
                has_ui_connection=True,
                has_agent_process=False,
                events_recent=False,
                boot_grace=False,
            ),
            "keep",
        ),
        (
            dict(
                idle_minutes=45,
                max_uptime_hours=12,
                minutes_since_active=10,
                uptime_minutes=30,
                has_ui_connection=False,
                has_agent_process=True,
                events_recent=False,
                boot_grace=False,
            ),
            "keep",
        ),
        (
            dict(
                idle_minutes=45,
                max_uptime_hours=12,
                minutes_since_active=5,
                uptime_minutes=10,
                has_ui_connection=False,
                has_agent_process=False,
                events_recent=False,
                boot_grace=True,
            ),
            "keep",
        ),
        (
            dict(
                idle_minutes=45,
                max_uptime_hours=12,
                minutes_since_active=50,
                uptime_minutes=60,
                has_ui_connection=False,
                has_agent_process=False,
                events_recent=False,
                boot_grace=False,
            ),
            "poweroff_idle",
        ),
        (
            dict(
                idle_minutes=45,
                max_uptime_hours=12,
                minutes_since_active=10,
                uptime_minutes=12 * 60 + 1,
                has_ui_connection=False,
                has_agent_process=False,
                events_recent=False,
                boot_grace=False,
            ),
            "poweroff_max_uptime",
        ),
        (
            dict(
                idle_minutes=45,
                max_uptime_hours=12,
                minutes_since_active=10,
                uptime_minutes=12 * 60 + 1,
                has_ui_connection=True,
                has_agent_process=False,
                events_recent=False,
                boot_grace=False,
            ),
            "keep",
        ),
    ],
)
def test_decide_table(kwargs: dict, expected: str) -> None:
    mod = _load()
    assert mod.decide(mod.IdleInputs(**kwargs)) == expected
