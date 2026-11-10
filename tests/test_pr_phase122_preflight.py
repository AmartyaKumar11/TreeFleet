"""Preflight names the worker interpreter and the check interpreter."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from treefleet.checks import interpreter_checks

pytestmark = pytest.mark.phase11

ROOT = Path(__file__).resolve().parents[1]


def test_preflight_names_the_crewai_and_pytest_interpreters() -> None:
    folder = "Scripts" if sys.platform == "win32" else "bin"
    name = "python.exe" if sys.platform == "win32" else "python"
    worker = ROOT / ".venv-worker" / folder / name
    rows = interpreter_checks(ROOT, str(worker))
    text = "\n".join(line for _ok, line in rows)
    assert all(ok for ok, _line in rows)
    assert f"worker interpreter imports crewai ({worker})" in text
    assert "check interpreter imports pytest" in text
