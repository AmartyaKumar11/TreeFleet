"""Board lane derivation from events and report fields."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from treefleet.ui.lanes import TaskLaneFacts, derive_lane, tasks_from_snapshot
from treefleet.ui.server import _stage

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "a2f1be0b-events.jsonl"
_CONTINUED = {"cursor.tool_start", "output_line", "review_started", "review_verdict"}

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


def test_approve_is_ready_to_merge() -> None:
    assert derive_lane(TaskLaneFacts(verdict="approve")) == "ready"
    assert derive_lane(TaskLaneFacts(integrated=True)) == "working"


def test_a_recovered_tool_denial_stays_on_that_task() -> None:
    denial = {
        "type": "guard.decision",
        "task_id": "alpha",
        "payload": {"stage": "tool_result", "allowed": False, "reasons": ["bad args"]},
    }
    stopped = tasks_from_snapshot(
        [
            {"type": "worker.spawned", "task_id": "alpha"},
            {"type": "worker.spawned", "task_id": "beta"},
            denial,
        ],
        None,
        awaiting=False,
    )
    lanes = {card["id"]: card["lane"] for card in stopped}
    assert lanes == {"alpha": "needs", "beta": "working"}
    recovered = tasks_from_snapshot(
        [
            {"type": "worker.spawned", "task_id": "alpha"},
            denial,
            {"type": "cursor.tool_start", "task_id": "alpha", "payload": {"tool": "read"}},
        ],
        None,
        awaiting=False,
    )
    assert recovered[0]["lane"] == "working"
    failed = tasks_from_snapshot(
        [denial, {"type": "worker.exited", "task_id": "alpha", "payload": {"status": "failed"}}],
        None,
        awaiting=False,
    )
    assert failed[0]["lane"] == "needs"


def _fixture_events() -> tuple[list[dict], int]:
    rows: list[dict] = []
    corrupt = 0
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            corrupt += 1
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows, corrupt


def test_live_run_a2f1be0b_lanes_follow_the_fleet() -> None:
    """Replay the first live run, including its corrupt events line."""
    rows, corrupt = _fixture_events()
    assert corrupt == 1
    seen = {"working": False, "review": False, "ready": False}
    prefix: list[dict] = []
    for item in rows:
        prefix.append(item)
        kind = item.get("type")
        task_id = item.get("task_id")
        if not isinstance(task_id, str) or kind not in _CONTINUED:
            continue
        lane = next(card["lane"] for card in tasks_from_snapshot(prefix, None) if card["id"] == task_id)
        raw_payload = item.get("payload")
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        if kind in {"cursor.tool_start", "output_line"}:
            assert lane == "working"
            seen["working"] = True
        elif kind == "review_started":
            assert lane == "review"
            seen["review"] = True
        elif payload.get("verdict") == "approve":
            assert lane == "ready"
            seen["ready"] = True
    assert seen == {"working": True, "review": True, "ready": True}
    assert _stage(rows, False, True) == "done"


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
