"""Rehearsal 59180665: Ready waits for an open green pull request, and the strip says so."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from treefleet.ui.lanes import status_line, tasks_from_snapshot
from treefleet.ui.server import _stage

pytestmark = pytest.mark.phase11

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "59180665-events.jsonl"


def _events() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


def _before(events: list[dict], kind: str) -> list[dict]:
    cut = next(index for index, event in enumerate(events) if event.get("type") == kind)
    return events[:cut]


def _cards(events: list[dict], *, pr_enabled: bool) -> list[dict]:
    return tasks_from_snapshot(events, None, pr_enabled=pr_enabled)


def test_opening_a_pull_request_stays_in_review_until_checks_are_green() -> None:
    events = _events()
    opening = _before(events, "pr.opened")
    pending = _before(events, "pr.checks_green")
    green = events

    cards = _cards(opening, pr_enabled=True)
    assert cards
    assert {card["lane"] for card in cards} == {"review"}
    assert {card.get("pr_chip") for card in cards} == {"opening PR"}
    assert status_line(_stage(opening, False, False), cards, pr_mode=True) == ("Reviewing", "review")
    unchanged = _cards(opening, pr_enabled=False)
    assert {card["lane"] for card in unchanged} == {"ready"}
    assert status_line(_stage(opening, False, False), unchanged, pr_mode=False) is None

    waiting = _cards(pending, pr_enabled=True)
    assert {card["lane"] for card in waiting} == {"review"}
    assert all(card.get("pr_number") for card in waiting)
    assert status_line(_stage(pending, False, False), waiting, pr_mode=True) == ("PRs open", "pr")

    ready = _cards(green, pr_enabled=True)
    assert {card["lane"] for card in ready} == {"ready"}
    assert status_line(_stage(green, False, False), ready, pr_mode=True) == ("2 ready to merge", "pr")

    merged_events = [
        *green,
        {"type": "pr.merged", "task_id": ready[0]["id"], "payload": {"number": ready[0]["pr_number"]}},
        {"type": "pr.merged", "task_id": ready[1]["id"], "payload": {"number": ready[1]["pr_number"]}},
    ]
    merged = _cards(merged_events, pr_enabled=True)
    assert {card["lane"] for card in merged} == {"done"}
    assert status_line("pr", merged, pr_mode=True) == ("2 merged", "pr")
    assert status_line("done", merged, pr_mode=True) == ("done", "pr")
