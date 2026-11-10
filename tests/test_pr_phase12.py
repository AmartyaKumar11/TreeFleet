"""Rehearsal 59180665: Ready waits for an open green pull request, and the strip says so."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from treefleet.ui.lanes import tasks_from_snapshot

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
    green = events

    cards = _cards(opening, pr_enabled=True)
    assert cards
    assert {card["lane"] for card in cards} == {"review"}
    assert {card.get("pr_chip") for card in cards} == {"opening PR"}
    unchanged = _cards(opening, pr_enabled=False)
    assert {card["lane"] for card in unchanged} == {"ready"}

    ready = _cards(green, pr_enabled=True)
    assert {card["lane"] for card in ready} == {"ready"}
