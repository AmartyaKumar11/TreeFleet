"""Preflight names the worker interpreter and the check interpreter."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from treefleet.checks import interpreter_checks
from treefleet.demo import preflight
from treefleet.pr.config import PrSettings
from treefleet.pr.trust import trust_line

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


def test_preflight_prints_trusted_commenters_and_fails_when_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty, missing = trust_line(PrSettings(enabled=True, repo="octo/demo", base="main"), "")
    assert not empty and "trusted commenters: (none)" in missing and "gh login (unresolved)" in missing
    ok, named = trust_line(
        PrSettings(enabled=True, repo="octo/demo", base="main", trusted_commenters=("sam",)),
        "ada",
    )
    assert ok and named == "green  trusted commenters: sam; gh login ada"
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "demo-base"], cwd=repo, check=True, shell=False)
    monkeypatch.setattr(
        "treefleet.demo.load_settings",
        lambda _path=None: type(
            "Settings",
            (),
            {
                "cursor": type("Cursor", (), {"cli": "cursor-agent"})(),
                "pr": PrSettings(enabled=True, repo="octo/demo", base="demo-base"),
                "worker_python": "python",
            },
        )(),
    )
    monkeypatch.setattr("treefleet.checks.interpreter_checks", lambda *_args, **_kwargs: [])

    def gh(args: list[str]) -> tuple[int, str, str]:
        if args[:3] == ["api", "user", "--jq"]:
            return 0, "ada\n", ""
        return 0, "", ""

    rows = preflight(
        repo,
        env={"DEEPSEEK_API_KEY": "present", "TYPESAFE_API_KEY": "present"},
        login=lambda _cli: (True, ""),
        models=lambda _cli: (True, ""),
        gh=gh,
    )
    text = "\n".join(line for _ok, line in rows)
    assert "green  trusted commenters: ada; gh login ada" in text
