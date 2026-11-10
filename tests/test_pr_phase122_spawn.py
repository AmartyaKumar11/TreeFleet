"""The worker process is the CrewAI interpreter for the fleet and for a revision."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from treefleet.config import load_settings
from treefleet.worker.spawner import crew_python, worker_command

pytestmark = pytest.mark.phase11

ROOT = Path(__file__).resolve().parents[1]


def _settings():
    os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
    os.environ.setdefault("TYPESAFE_API_KEY", "test-key")
    return load_settings(ROOT / "treefleet.toml")


def test_fleet_and_revision_argv_import_crewai(tmp_path: Path) -> None:
    settings = _settings()
    python = crew_python(settings.worker_python)
    recorded = Path(settings.orchestrator_python)
    assert python != recorded
    folder = "Scripts" if sys.platform == "win32" else "bin"
    for _path in ("fleet", "revision"):
        argv = worker_command(
            python=python,
            worktree=tmp_path,
            run_id="59180665",
            task_id="add-notifications-unit-tests",
            token="test-token",
            port=9,
            events=tmp_path / "events.jsonl",
        )
        assert Path(argv[0]) == python
        assert argv[1:3] == ["-m", "treefleet.worker.main"]
        assert folder in str(python)
        proc = subprocess.run(
            [argv[0], "-c", "import treefleet.worker.crew"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            shell=False,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        assert proc.returncode == 0
