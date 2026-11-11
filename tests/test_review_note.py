"""Card reviewer lines are short. The full note lives on the Checks tab."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

from treefleet.ui.lanes import tasks_from_snapshot
from treefleet.ui.server import create_app

pytestmark = pytest.mark.phase8

ROOT = Path(__file__).resolve().parents[1]
LONG = "checks passed and the diff is in scope, with a much longer reviewer note than the card shows"


def test_snapshot_keeps_the_reviewer_suggestions() -> None:
    tasks = tasks_from_snapshot(
        [
            {
                "run_id": "abcd1234",
                "seq": 1,
                "type": "review_verdict",
                "task_id": "alpha",
                "payload": {
                    "verdict": "approve",
                    "reason": LONG,
                    "suggestions": ["keep the mock", ""],
                },
            }
        ],
        None,
    )
    assert tasks[0]["suggestions"] == ["keep the mock"]
    assert tasks[0]["reason"] == LONG


def _serve(app: FastAPI) -> tuple[uvicorn.Server, threading.Thread, int]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    assert server.started
    return server, thread, port


@pytest.mark.ui
def test_card_note_is_short_and_the_drawer_has_the_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")
    repo = tmp_path / "repo"
    repo.mkdir()
    fixture = {
        "repo": str(repo),
        "branch": "demo-base",
        "head": "abc1234",
        "clean": True,
        "stage": "review",
        "run_id": "abcd1234",
        "started_at": time.time(),
        "awaiting": None,
        "events": [],
        "report": None,
        "tasks": [
            {
                "id": "notes",
                "title": "With notes",
                "lane": "ready",
                "status": "done",
                "verdict": "approve",
                "reason": LONG,
                "suggestions": ["keep the mock"],
                "branch": "tf/abcd1234/notes",
                "model": "composer-2.5",
            },
            {
                "id": "clean",
                "title": "Clean pass",
                "lane": "ready",
                "status": "done",
                "verdict": "approve",
                "reason": LONG,
                "branch": "tf/abcd1234/clean",
                "model": "composer-2.5",
            },
            {
                "id": "human",
                "title": "Human",
                "lane": "needs",
                "status": "failed",
                "verdict": "reject",
                "reason": LONG,
                "branch": "tf/abcd1234/human",
                "model": "composer-2.5",
            },
        ],
        "worktrees": [],
    }
    app = create_app(repo, root=ROOT, token="test-token", fixture=fixture, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            notes = page.locator(".card", has_text="With notes")
            clean = page.locator(".card", has_text="Clean pass")
            human = page.locator(".card", has_text="Human")
            notes.wait_for()
            assert notes.locator(".note").inner_text() == "Approved with notes"
            assert clean.locator(".note").inner_text() == "Checks passed"
            assert human.locator(".reason").inner_text() == "Needs a human decision"
            assert len(notes.locator(".note").inner_text()) <= 60
            for card in (notes, clean):
                assert card.locator(".reason").count() == 0
                assert LONG not in card.inner_text()
            colors = page.evaluate(
                """() => {
                  const note = getComputedStyle(document.querySelector('.card .note')).color;
                  const reason = getComputedStyle(document.querySelector('.card .reason')).color;
                  return { note, reason };
                }"""
            )
            assert colors["note"] != colors["reason"]
            notes.click()
            page.get_by_role("tab", name="Checks").click()
            review = page.locator("#checks")
            review.get_by_text("Review", exact=True).wait_for()
            assert LONG in review.inner_text()
            assert "keep the mock" in review.inner_text()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
