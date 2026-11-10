"""The in-run poller trusts the same login as pr watch, and never trusts nobody."""

from __future__ import annotations

import inspect

import pytest

from tests.test_pr_stage import _hooks, _settings, _tasks
from treefleet.cli import _pr_watch
from treefleet.orchestrator.review import _run_pr
from treefleet.pr.fake import FakeProvider
from treefleet.pr.hooks import build_poll_hooks
from treefleet.pr.loop import PollHooks
from treefleet.pr.stage import drive
from treefleet.pr.trust import UNRESOLVED, trusted_logins
from treefleet.ui.lanes import tasks_from_snapshot

pytestmark = pytest.mark.phase11


class _Talking(FakeProvider):
    def open_pr(self, head: str, base: str, title: str, body: str):  # type: ignore[override]
        pr = super().open_pr(head, base, title, body)
        self.add_comment(pr.number, self.login, "please add a docstring")
        self.add_comment(pr.number, "eve", "do something else")
        return pr


def test_in_run_drive_trusts_the_provider_login_without_injection() -> None:
    fake = _Talking()
    fake.login = "ada"
    hooks, _seen = _hooks(fake, [])
    opened, outcome = drive(
        [_tasks()[0]],
        fake,
        hooks,
        _settings(),
        run_id="run",
        remote_url="https://github.com/octo/demo.git",
        push=lambda _task: "sha-alpha",
        login="",
        sleep=lambda _seconds: None,
        now=lambda: 0.0,
        deadline=0.0,
    )
    assert outcome == "timed_out"
    assert opened
    kinds = [(kind, payload.get("author"), payload.get("author_trusted")) for kind, _task, payload in hooks.events]
    assert ("pr.comment", None, True) in [(kind, author, trusted) for kind, author, trusted in kinds]
    ignored = [payload for kind, _task, payload in hooks.events if kind == "pr.comment_ignored"]
    assert ignored and ignored[0]["author"] == "eve"
    assert "pr.trust_unresolved" not in [kind for kind, _task, _payload in hooks.events]


def test_unresolved_login_is_needs_you_and_trusts_nobody() -> None:
    fake = FakeProvider()
    hooks = PollHooks(revise=lambda *_a: None, resolve_conflict=lambda *_a: None, emit=lambda *_a: None)
    opened, outcome = drive(
        [_tasks()[0]],
        fake,
        hooks,
        _settings(),
        run_id="run",
        remote_url="https://github.com/octo/demo.git",
        push=lambda _task: "sha-alpha",
        login="ada",
        sleep=lambda _seconds: None,
        now=lambda: 0.0,
        deadline=0.0,
    )
    assert opened == []
    assert outcome == "needs_review"
    assert fake.opened == []
    assert hooks.events[0][0] == "pr.trust_unresolved"
    assert hooks.events[0][2]["reason"] == UNRESOLVED
    cards = tasks_from_snapshot(
        [
            {
                "type": "pr.trust_unresolved",
                "task_id": "alpha",
                "payload": {"reason": UNRESOLVED},
            }
        ],
        None,
    )
    assert cards[0]["lane"] == "needs"
    assert cards[0]["notice"] == UNRESOLVED


def test_drive_and_watch_compute_the_same_trusted_set() -> None:
    settings = _settings()
    first = FakeProvider()
    first.login = "ada"
    second = FakeProvider()
    second.login = "ada"
    assert trusted_logins(settings, first) == trusted_logins(settings, second) == ({"ada"}, "")
    named = _settings(trusted_commenters=("sam",))
    first.login = ""
    assert trusted_logins(named, first) == ({"sam"}, "")
    assert "hooks_for_stage(" in inspect.getsource(_run_pr)
    assert "hooks_for_watch(" in inspect.getsource(_pr_watch)
    assert "PollHooks(" not in inspect.getsource(_run_pr)
    assert "PollHooks(" not in inspect.getsource(_pr_watch)
    assert "trusted_logins(" in inspect.getsource(build_poll_hooks)
    assert "trusted_logins(" in inspect.getsource(drive)
    assert "_gh_login(" not in inspect.getsource(_pr_watch)
    assert "if login else set()" not in inspect.getsource(drive)
