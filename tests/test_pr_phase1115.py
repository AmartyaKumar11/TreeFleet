"""A check that cannot start, and the two-round comment budget."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from treefleet.checks import run_command, unavailable_reason
from treefleet.pr.fake import FakeProvider
from treefleet.pr.loop import CheckUnavailable, PollHooks, _satisfy
from treefleet.pr.models import OpenPR, TrackedComment
from treefleet.reactions import default_table
from treefleet.ui.lanes import tasks_from_snapshot

pytestmark = pytest.mark.phase11

ROOT = Path(__file__).resolve().parents[1]


def test_path_without_python_is_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Windows searches the launching interpreter's directory before PATH, so a
    # bare "python" still resolves there. The token below is on neither.
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    code, out, err = run_command("treefleet-missing-python -m pytest -q", tmp_path)
    reason = unavailable_reason([SimpleNamespace(name="pytest", exit_code=code, stdout_tail=out, stderr_tail=err)])
    assert code == 127
    assert reason == "pytest: command not found"
    card = tasks_from_snapshot(
        [{"type": "check.unavailable", "task_id": "alpha", "payload": {"task": "alpha", "reason": reason}}],
        None,
    )[0]
    assert card["lane"] == "needs"
    assert card["reason"] == reason


def test_interpreter_without_pytest_is_unavailable(tmp_path: Path) -> None:
    folder = "Scripts" if sys.platform == "win32" else "bin"
    name = "python.exe" if sys.platform == "win32" else "python"
    exe = ROOT / ".venv-worker" / folder / name
    assert exe.is_file()
    code, out, err = run_command(f'"{exe}" -m pytest -q', tmp_path)
    reason = unavailable_reason([SimpleNamespace(name="pytest", exit_code=code, stdout_tail=out, stderr_tail=err)])
    assert reason == "pytest: module missing"
    card = tasks_from_snapshot(
        [{"type": "check.unavailable", "task_id": "alpha", "payload": {"task": "alpha", "reason": reason}}],
        None,
    )[0]
    assert card["lane"] == "needs"
    assert card["reason"] == reason


def _item(fake: FakeProvider) -> OpenPR:
    opened = fake.open_pr("tf/run/alpha", "demo-base", "Alpha", "body")
    return OpenPR(
        task_id="alpha",
        number=opened.number,
        url=opened.url,
        head=opened.head,
        head_sha=opened.head_sha,
    )


def test_two_unavailable_checks_do_not_spend_the_commit_round() -> None:
    fake = FakeProvider()
    item = _item(fake)
    tracked = TrackedComment(id=7, body="add the docstring")
    calls = {"n": 0}

    def revise(_task_id: str, _feedback: str) -> str:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise CheckUnavailable("pytest: module missing")
        return "abc123"

    hooks = PollHooks(
        revise=revise,
        resolve_conflict=lambda _task: "abc123",
        emit=lambda *_args: None,
        judge=lambda *_args: ("satisfied", "matches the request"),
        diff=lambda _task: "tests/test_admin.py | 1 +",
    )
    for _turn in range(2):
        item.status = "open"
        _satisfy(item, tracked, fake, default_table(), hooks)
        assert tracked.rounds_used == 0
        assert item.status == "needs_review"
    item.status = "open"
    _satisfy(item, tracked, fake, default_table(), hooks)
    assert calls["n"] == 3
    assert tracked.rounds_used == 1
    assert tracked.status == "satisfied"
    assert any(event[0] == "check.unavailable" for event in hooks.events)
    assert not any(event[0] == "pr.revision_failed" for event in hooks.events)


def test_two_no_commit_rounds_block_a_third() -> None:
    fake = FakeProvider()
    item = _item(fake)
    tracked = TrackedComment(id=8, body="add the docstring")
    calls = {"n": 0}

    def revise(_task_id: str, _feedback: str) -> None:
        calls["n"] += 1
        return None

    hooks = PollHooks(
        revise=revise,
        resolve_conflict=lambda _task: "",
        emit=lambda *_args: None,
        judge=lambda *_args: ("satisfied", "matches the request"),
    )
    _satisfy(item, tracked, fake, default_table(), hooks)
    assert calls["n"] == 2
    assert tracked.rounds_used == 2
    assert tracked.status == "needs_human"
    assert item.status == "needs_review"
    _satisfy(item, tracked, fake, default_table(), hooks)
    assert calls["n"] == 2
