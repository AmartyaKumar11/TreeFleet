"""A worker that dies before its first step is not a revision round."""

from __future__ import annotations

import pytest

from treefleet.pr.fake import FakeProvider
from treefleet.pr.loop import PollHooks, WorkerStartFailed, _satisfy
from treefleet.pr.models import OpenPR, TrackedComment
from treefleet.reactions import default_table
from treefleet.ui.lanes import tasks_from_snapshot

pytestmark = pytest.mark.phase11


def test_spawn_failure_leaves_the_round_and_does_not_retry() -> None:
    fake = FakeProvider()
    opened = fake.open_pr("tf/59180665/add-notifications-unit-tests", "demo-rehearsal", "Notes", "body")
    item = OpenPR(
        task_id="add-notifications-unit-tests",
        number=opened.number,
        url=opened.url,
        head=opened.head,
        head_sha=opened.head_sha,
    )
    tracked = TrackedComment(id=6760374466, body="Please add a one-line module docstring.")
    calls = {"n": 0}

    def revise(_task_id: str, _feedback: str) -> None:
        calls["n"] += 1
        raise WorkerStartFailed("ModuleNotFoundError: No module named 'crewai'")

    hooks = PollHooks(revise=revise, resolve_conflict=lambda _task: None, emit=lambda *_args: None)
    _satisfy(item, tracked, fake, default_table(), hooks)
    assert calls["n"] == 1
    assert tracked.rounds_used == 0
    assert tracked.status == "needs_human"
    failed = [event for event in hooks.events if event[0] == "worker.start_failed"]
    assert len(failed) == 1
    assert failed[0][2]["task"] == "add-notifications-unit-tests"
    assert failed[0][2]["reason"] == "ModuleNotFoundError: No module named 'crewai'"
    assert not any(event[0] == "pr.revision_failed" for event in hooks.events)
    card = tasks_from_snapshot(
        [{"type": "worker.start_failed", "task_id": item.task_id, "payload": failed[0][2]}],
        None,
    )[0]
    assert card["lane"] == "needs"
    assert card["reason"] == "worker could not start: ModuleNotFoundError: No module named 'crewai'"
