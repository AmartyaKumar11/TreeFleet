"""15.D10: git subprocess env keeps GH_TOKEN/HOME for the gh credential helper."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from treefleet.git.worktrees import git_environ, run_git


def test_git_environ_keeps_gh_token_and_home_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/treefleet")
    monkeypatch.setenv("GH_TOKEN", "test-gh-token-not-a-real-secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    env = git_environ()
    assert env["HOME"] == "/home/treefleet"
    assert env["GH_TOKEN"] == "test-gh-token-not-a-real-secret"
    assert env["GIT_TERMINAL_PROMPT"] == "0"


@pytest.mark.skipif(sys.platform == "win32", reason="Linux credential path under test")
def test_run_git_passes_credential_env_to_subprocess(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("HOME", "/home/treefleet")
    monkeypatch.setenv("GH_TOKEN", "test-gh-token-not-a-real-secret")
    captured: dict[str, object] = {}

    def fake_run(*_args, **kwargs):
        captured["env"] = kwargs.get("env")
        return MagicMock(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("treefleet.git.worktrees.subprocess.run", fake_run)
    run_git(["status"], cwd=tmp_path)
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["GH_TOKEN"] == "test-gh-token-not-a-real-secret"
    assert env["HOME"] == "/home/treefleet"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
