"""Board lane derivation from events and report fields."""

from __future__ import annotations

from pathlib import Path

import pytest

from treefleet.ui.lanes import TaskLaneFacts, derive_lane, tasks_from_snapshot
from treefleet.ui.server import _stage

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "a2f1be0b-events.jsonl"
STUB = Path(__file__).resolve().parents[1] / "evidence" / "phase-8" / "ui-v2" / "live-real-2" / "stub" / "events.jsonl"
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
        (TaskLaneFacts(verdict="approve", pr_enabled=True), "review"),
        (TaskLaneFacts(verdict="approve", ready_to_merge=True), "ready"),
        (TaskLaneFacts(pr_status="merged", verdict="approve", status="done"), "done"),
        (TaskLaneFacts(pr_status="cancelled", verdict="approve"), "needs"),
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


def _history(rows: list[dict]) -> dict[str, list[str]]:
    history: dict[str, list[str]] = {}
    prefix: list[dict] = []
    for item in rows:
        prefix.append(item)
        for card in tasks_from_snapshot(prefix, None):
            seen = history.setdefault(card["id"], [])
            if not seen or seen[-1] != card["lane"]:
                seen.append(card["lane"])
    return history


def test_a_recovered_tool_denial_never_moves_the_card() -> None:
    argument = {
        "type": "guard.decision",
        "task_id": "alpha",
        "payload": {"stage": "tool_result", "allowed": False, "reasons": ["bad args"]},
    }
    tool_error = {
        "type": "tool.error",
        "task_id": "alpha",
        "payload": {"tool": "read_file", "message": "path is required"},
    }
    kept = tasks_from_snapshot(
        [
            {"type": "worker.spawned", "task_id": "alpha"},
            {"type": "worker.spawned", "task_id": "beta"},
            argument,
            tool_error,
        ],
        None,
        awaiting=False,
    )
    assert {card["id"]: card["lane"] for card in kept} == {"alpha": "working", "beta": "working"}
    policy = {
        "type": "guard.decision",
        "task_id": "alpha",
        "payload": {"stage": "tool_args", "allowed": False, "reasons": ["path rejected: parent"]},
    }
    recovered = tasks_from_snapshot(
        [
            {"type": "worker.spawned", "task_id": "alpha"},
            policy,
            {"type": "cursor.tool_start", "task_id": "alpha", "payload": {"tool": "read"}},
        ],
        None,
        awaiting=False,
    )
    assert recovered[0]["lane"] == "working"
    assert (
        "needs"
        not in _history(
            [
                {"type": "worker.spawned", "task_id": "alpha"},
                policy,
                {"type": "cursor.tool_start", "task_id": "alpha", "payload": {"tool": "read"}},
                {"type": "worker.exited", "task_id": "alpha", "payload": {"status": "done"}},
                {"type": "review_verdict", "task_id": "alpha", "payload": {"verdict": "approve"}},
            ]
        )["alpha"]
    )
    failed = tasks_from_snapshot(
        [
            {"type": "worker.spawned", "task_id": "alpha"},
            policy,
            {"type": "worker.exited", "task_id": "alpha", "payload": {"status": "failed"}},
        ],
        None,
        awaiting=False,
    )
    assert failed[0]["lane"] == "needs"
    human = tasks_from_snapshot(
        [
            {"type": "worker.spawned", "task_id": "alpha"},
            {
                "type": "reaction.fired",
                "task_id": "alpha",
                "payload": {"action": "needs_human", "event": "review_rejected"},
            },
        ],
        None,
        awaiting=False,
    )
    assert human[0]["lane"] == "needs"


def _fixture_events() -> tuple[list[dict], int]:
    from treefleet.events import read_events

    return read_events(FIXTURE)


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
    _assert_approved_never_needed(rows)


def _assert_approved_never_needed(rows: list[dict]) -> None:
    history = _history(rows)
    final = {card["id"]: card for card in tasks_from_snapshot(rows, None)}
    for task_id, card in final.items():
        if card.get("verdict") == "approve" or card.get("status") == "done":
            assert "needs" not in history[task_id], history[task_id]


def test_stub_fleet_approved_tasks_never_visit_needs_you() -> None:
    from treefleet.events import read_events

    rows, _corrupt = read_events(STUB)
    _assert_approved_never_needed(rows)


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
