"""Replay of run c6dbaa59. The latest pr fact chooses the lane."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from treefleet.ui.lanes import tasks_from_snapshot

pytestmark = pytest.mark.phase11

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "c6dbaa59-events.jsonl"


def test_replay_puts_merged_in_done_and_open_green_in_ready() -> None:
    events = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
    cards = {card["id"]: card for card in tasks_from_snapshot(events, None)}
    merged = cards["test-notifications"]
    open_green = cards["test-admin"]
    assert merged["pr_number"] == 85
    assert merged["pr_status"] == "merged"
    assert merged["lane"] == "done"
    assert open_green["pr_number"] == 84
    assert open_green["pr_status"] == "approved_green"
    assert open_green["lane"] == "ready"


def test_closed_unmerged_is_needs_you_with_the_reason() -> None:
    events = [
        {"type": "pr.opened", "task_id": "alpha", "payload": {"number": 9}},
        {"type": "review_verdict", "task_id": "alpha", "payload": {"verdict": "approve"}},
        {
            "type": "pr.closed",
            "task_id": "alpha",
            "payload": {"number": 9, "reason": "pull request closed"},
        },
    ]
    card = tasks_from_snapshot(events, None)[0]
    assert card["lane"] == "needs"
    assert card["pr_status"] == "cancelled"
    assert card["reason"] == "pull request closed"
