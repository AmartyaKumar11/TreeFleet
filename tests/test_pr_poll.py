"""Poller retries, and preflight reads only the sentinel head."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from treefleet.pr.config import PrSettings
from treefleet.pr.loop import PollHooks
from treefleet.pr.models import CheckRun, OpenPR, PRState
from treefleet.pr.preflight import PreflightError, gh_smoke, preflight
from treefleet.pr.stage import poll_until
from treefleet.ui.lanes import tasks_from_snapshot

pytestmark = pytest.mark.phase11


class _Flaky:
    def __init__(self, failures: int, *, final: str = "merged") -> None:
        self.left = failures
        self.final = final
        self.calls = 0

    def get_pr(self, number: int) -> PRState:
        self.calls += 1
        if self.left:
            self.left -= 1
            raise RuntimeError("HTTP 503 sk-abcdefghij")
        return PRState(
            number=number,
            state=self.final,
            mergeable="MERGEABLE",
            check_runs=(CheckRun("test", "COMPLETED", "success"),),
            head_sha="abc",
            url="",
        )

    def list_comments(self, _number: int, _since: int) -> list[object]:
        return []


def _item() -> OpenPR:
    return OpenPR(task_id="test-notifications", number=85, url="", head="tf/run/test-notifications", head_sha="abc")


def _hooks() -> PollHooks:
    return PollHooks(revise=lambda _task, _feedback: None, resolve_conflict=lambda _task: None, emit=lambda *_a: None)


def _settings() -> PrSettings:
    return PrSettings(enabled=True, repo="octo/demo", poll_seconds=15, max_wait_minutes=20)


def test_poll_survives_three_failures_then_a_merged_pull_request() -> None:
    sleeps: list[float] = []
    provider = _Flaky(3)
    opened = [_item()]
    hooks = _hooks()
    outcome = poll_until(
        opened,
        provider,
        hooks,
        _settings(),
        trusted=set(),
        sleep=sleeps.append,
        now=lambda: 0.0,
        deadline=10_000.0,
    )
    assert outcome == "done"
    assert sleeps == [5.0, 15.0, 45.0]
    assert [event[0] for event in hooks.events if event[0] == "pr.poll_error"] == ["pr.poll_error"] * 3
    assert "sk-abcdefghij" not in json.dumps(hooks.events)
    assert hooks.events[0][2]["consecutive"] == 1
    assert hooks.events[2][2]["consecutive"] == 3
    assert opened[0].status == "merged"


def test_five_poll_failures_stop_and_notify() -> None:
    sleeps: list[float] = []
    provider = _Flaky(99)
    opened = [_item()]
    hooks = _hooks()
    outcome = poll_until(
        opened,
        provider,
        hooks,
        _settings(),
        trusted=set(),
        sleep=sleeps.append,
        now=lambda: 0.0,
        deadline=10_000.0,
    )
    assert outcome == "pr_poll_failed"
    assert sleeps == [5.0, 15.0, 45.0, 45.0]
    errors = [event for event in hooks.events if event[0] == "pr.poll_error"]
    assert [event[2]["consecutive"] for event in errors] == [1, 2, 3, 4, 5]
    assert opened[0].status == "needs_review"
    failed = [event for event in hooks.events if event[0] == "pr.poll_failed"]
    assert failed[0][2]["reason"] == "PR polling failed"
    notify = [event for event in hooks.events if event[0] == "reaction.fired"]
    assert notify[0][2]["action"] == "notify"
    cards = tasks_from_snapshot(
        [
            {
                "type": "pr.poll_failed",
                "task_id": "test-notifications",
                "payload": {"reason": "PR polling failed", "number": 85},
            }
        ],
        None,
        awaiting=False,
    )
    assert cards[0]["lane"] == "needs"
    assert cards[0]["reason"] == "PR polling failed"


def test_preflight_smoke_lists_only_the_sentinel_head() -> None:
    calls: list[list[str]] = []

    def runner(args: list[str]) -> tuple[int, str, str]:
        calls.append(list(args))
        if args[:2] == ["auth", "status"]:
            return 0, "", ""
        if args[:2] == ["api", "user"]:
            return 0, "ada", ""
        if args[:3] == ["api", "repos/octo/demo", "--jq"]:
            return 0, "true", ""
        if args[:2] == ["api", "repos/octo/demo"]:
            return 0, "{}", ""
        if "/branches/" in args[1]:
            return 0, "", ""
        if "workflows" in args[1]:
            return 0, "[]", ""
        if args[:2] == ["pr", "list"]:
            return 0, "[]", ""
        return 1, "", "unexpected"

    settings = PrSettings(enabled=True, repo="octo/demo", base="demo-base")
    warnings = preflight(
        Path("."), settings, runner=runner, remote_url="https://github.com/octo/demo.git", hook_hosts=[]
    )
    assert warnings == [f"warning: no workflow on {settings.base}"]
    listed = [args for args in calls if args[:2] == ["pr", "list"]]
    assert listed == [["pr", "list", "--repo", "octo/demo", "--head", "tf/__preflight__", "--json", "number"]]
    assert ["api", "repos/octo/demo"] in calls
    assert not any("view" in args or (len(args) > 2 and args[2].isdigit()) for args in calls)

    def busy(args: list[str]) -> tuple[int, str, str]:
        if args[:2] == ["pr", "list"]:
            return 0, '[{"number": 80}]', ""
        return 0, "{}", ""

    with pytest.raises(PreflightError, match="matched a pull request"):
        gh_smoke(settings, busy)
