"""15.D8: sandbox escape hatch and cursor-agent / agent PATH resolution."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from treefleet.cursor_run import _minimal_env, command_for, resolve_cursor_cli, run_cursor


def test_minimal_env_keeps_linux_home_locale_and_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/treefleet")
    monkeypatch.setenv("USER", "treefleet")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/home/treefleet/.config")
    monkeypatch.setenv("PATH", "/home/treefleet/.local/bin:/usr/bin")
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    monkeypatch.delenv("TREEFLEET_FAKE_EDIT", raising=False)
    env = _minimal_env()
    assert env["HOME"] == "/home/treefleet"
    assert env["USER"] == "treefleet"
    assert env["LANG"] == "C.UTF-8"
    assert env["XDG_CONFIG_HOME"] == "/home/treefleet/.config"
    assert "/home/treefleet/.local/bin" in env["PATH"]
    assert env["CURSOR_API_KEY"] == "test-key"
    assert env["NO_OPEN_BROWSER"] == "1"
    assert "DEEPSEEK_API_KEY" not in env


def test_run_cursor_passes_stdin_devnull(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Cursor -p hangs on an open stdin pipe (CLI changelog 2026-02); always DEVNULL."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    captured: dict[str, object] = {}

    class FakeProc:
        def __init__(self) -> None:
            self.stdout = MagicMock()
            self.returncode = 0

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def kill(self) -> None:
            return None

    def fake_popen(*args: object, **kwargs: object) -> FakeProc:
        captured.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr("treefleet.cursor_run._read_stdout", lambda *a, **k: (False, ""))
    monkeypatch.setattr("treefleet.cursor_run.revert_out_of_scope", lambda *a, **k: [])
    monkeypatch.setattr("treefleet.cursor_run.collect_changes", lambda *a, **k: [])
    monkeypatch.setattr("treefleet.cursor_run.record_step", lambda *a, **k: None)
    run_cursor(
        cli=tmp_path / "cursor-agent.py",
        worktree=worktree,
        instruction="hi",
        allowed=["README.md"],
        denied=[],
        timeout_s=5,
        model=None,
    )
    assert captured.get("stdin") is subprocess.DEVNULL


def test_sandbox_windows_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("TREEFLEET_CURSOR_SANDBOX", raising=False)
    shim = tmp_path / "cursor-agent.py"
    shim.write_text("print('ok')\n", encoding="utf-8")
    argv = command_for(shim, tmp_path / "wt", None, "do it")
    assert "--sandbox" in argv
    assert argv[argv.index("--sandbox") + 1] == "disabled"


def test_sandbox_linux_default_omits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("TREEFLEET_CURSOR_SANDBOX", raising=False)
    shim = tmp_path / "cursor-agent.py"
    shim.write_text("print('ok')\n", encoding="utf-8")
    argv = command_for(shim, tmp_path / "wt", None, "do it")
    assert "--sandbox" not in argv


def test_sandbox_linux_env_disables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("TREEFLEET_CURSOR_SANDBOX", "disabled")
    shim = tmp_path / "cursor-agent.py"
    shim.write_text("print('ok')\n", encoding="utf-8")
    argv = command_for(shim, tmp_path / "wt", None, "do it")
    assert "--sandbox" in argv
    assert argv[argv.index("--sandbox") + 1] == "disabled"


def test_resolve_falls_back_to_agent_on_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    agent = bindir / ("agent.cmd" if os.name == "nt" else "agent")
    agent.write_text("@echo off\n" if os.name == "nt" else "#!/bin/sh\necho agent\n", encoding="utf-8")
    if os.name != "nt":
        agent.chmod(agent.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bindir))
    # Ensure cursor-agent is not found
    resolved = resolve_cursor_cli("cursor-agent")
    assert resolved.resolve() == agent.resolve()


def test_resolve_prefers_cursor_agent_when_present(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    ca_name = "cursor-agent.cmd" if os.name == "nt" else "cursor-agent"
    ag_name = "agent.cmd" if os.name == "nt" else "agent"
    ca = bindir / ca_name
    ag = bindir / ag_name
    body = "@echo off\n" if os.name == "nt" else "#!/bin/sh\necho x\n"
    ca.write_text(body, encoding="utf-8")
    ag.write_text(body, encoding="utf-8")
    if os.name != "nt":
        ca.chmod(ca.stat().st_mode | stat.S_IXUSR)
        ag.chmod(ag.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bindir))
    resolved = resolve_cursor_cli("cursor-agent")
    assert resolved.resolve() == ca.resolve()
