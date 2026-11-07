"""Board lane derivation from events and report fields."""

from __future__ import annotations

import pytest

from treefleet.ui.lanes import TaskLaneFacts, derive_lane, tasks_from_snapshot

pytestmark = pytest.mark.phase8


@pytest.mark.parametrize(
    ("facts", "want"),
    [
        (TaskLaneFacts(awaiting_approval=True), "needs"),
        (TaskLaneFacts(status="needs_review"), "needs"),
        (TaskLaneFacts(checks_failed=True), "needs"),
        (TaskLaneFacts(conflict=True), "needs"),
        (TaskLaneFacts(guard_blocked=True), "needs"),
        (TaskLaneFacts(worker_active=True), "working"),
        (TaskLaneFacts(output_seen=True), "working"),
        (TaskLaneFacts(fleet_in_progress=True), "working"),
        (TaskLaneFacts(review_started=True), "review"),
        (TaskLaneFacts(awaiting_verdict=True), "review"),
        (TaskLaneFacts(status="done"), "review"),
        (TaskLaneFacts(verdict="approve", integrated=True), "ready"),
        (TaskLaneFacts(verdict="approve", ready_to_merge=True), "ready"),
    ],
)
def test_derive_lane_table_rows(facts: TaskLaneFacts, want: str) -> None:
    assert derive_lane(facts) == want


def test_needs_you_outranks_working_and_review() -> None:
    assert (
        derive_lane(
            TaskLaneFacts(
                checks_failed=True,
                worker_active=True,
                review_started=True,
                verdict="approve",
                integrated=True,
            )
        )
        == "needs"
    )


def test_ready_requires_approve_and_merge_signal() -> None:
    assert derive_lane(TaskLaneFacts(verdict="approve")) == "review"
    assert derive_lane(TaskLaneFacts(integrated=True)) == "working"


def test_tasks_from_snapshot_attach_lane() -> None:
    events = [
        {"type": "awaiting_approval", "payload": {"approved": False}},
        {"type": "worker.spawned", "task_id": "alpha"},
        {"type": "output_line", "task_id": "alpha", "payload": {"line": "editing"}},
        {"type": "worker.exited", "task_id": "alpha", "payload": {"status": "done"}},
        {"type": "review_started", "task_id": "alpha"},
        {
            "type": "review_verdict",
            "task_id": "alpha",
            "payload": {"verdict": "approve"},
        },
    ]
    report = {
        "results": [{"task_id": "alpha", "status": "done", "checks": []}],
        "integration": {"merged": ["alpha"], "conflicts": [], "excluded": []},
    }
    tasks = tasks_from_snapshot(events, report, awaiting=True)
    assert tasks[0]["id"] == "alpha"
    assert tasks[0]["lane"] == "needs"
    tasks_done = tasks_from_snapshot(events, report, awaiting=False)
    assert tasks_done[0]["lane"] == "ready"
