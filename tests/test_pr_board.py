"""Board chips and lanes for a fake pull-request scenario."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

from tests._evidence import evidence_path
from treefleet.ui.lanes import tasks_from_snapshot
from treefleet.ui.server import create_app

pytestmark = pytest.mark.phase11

ROOT = Path(__file__).resolve().parents[1]


def _events() -> list[dict[str, object]]:
    def event(kind: str, task_id: str, payload: dict[str, object]) -> dict[str, object]:
        return {"run_id": "run", "type": kind, "task_id": task_id, "payload": payload}

    return [
        event("review_verdict", "alpha", {"verdict": "approve", "reason": "checks passed"}),
        event("pr.opened", "alpha", {"number": 12, "url": "https://github.com/octo/demo/pull/12"}),
        event("review_verdict", "beta", {"verdict": "approve", "reason": "checks passed"}),
        event("pr.opened", "beta", {"number": 13, "url": "https://github.com/octo/demo/pull/13"}),
        event("pr.checks", "beta", {"checks": "passing"}),
        event("pr.comment_ignored", "alpha", {"author": "stranger"}),
        event("pr.revision_started", "gamma", {}),
        event("phase", "alpha", {"name": "integrate"}),
    ]


def test_pr_lanes_follow_the_events() -> None:
    tasks = {task["id"]: task for task in tasks_from_snapshot(_events(), None)}
    assert tasks["alpha"]["lane"] == "review"
    assert tasks["alpha"]["pr_number"] == 12
    assert tasks["beta"]["lane"] == "ready"
    assert tasks["beta"]["pr_checks"] == "passing"
    assert tasks["gamma"]["lane"] == "working"


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
def test_pr_board_at_both_widths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")
    repo = tmp_path / "repo"
    repo.mkdir()
    tasks = tasks_from_snapshot(_events(), None)
    for task in tasks:
        task["title"] = str(task["id"])
        task["branch"] = f"tf/run/{task['id']}"
        task["model"] = "composer-2.5"
    fixture = {
        "repo": str(repo),
        "branch": "demo-base",
        "head": "abc1234",
        "clean": True,
        "stage": "pr",
        "run_id": "run",
        "started_at": time.time(),
        "awaiting": None,
        "events": _events(),
        "report": {"schema_id": "report", "integration": {"branch": "tf/run/integrated"}},
        "tasks": tasks,
        "worktrees": [],
        "pr_mode": True,
        "pr_repo": "octo/demo",
    }
    app = create_app(repo, root=ROOT, token="test-token", fixture=fixture, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            review = page.locator("#lane-review")
            ready = page.locator("#lane-ready")
            review.get_by_text("PR #12").wait_for()
            assert "checks pending" in review.inner_text()
            assert "PR #13" in ready.inner_text()
            assert "checks passing" in ready.inner_text()
            assert ready.locator(".merge-btn").get_attribute("data-merge") == "gh pr merge 13 --repo octo/demo --squash"
            assert page.locator("[data-graph-integrated]").count() == 0
            assert page.locator('#stage-stepper li[data-stage="pr"]').inner_text() == "pr"
            page.screenshot(path=str(evidence_path("phase-11/board-1440.png")))
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(evidence_path("phase-11/board-390.png")))
            page.get_by_role("tab", name="Orchestrator").click()
            thread_text = page.locator("#orch-thread").inner_text()
            assert "pr.opened" in thread_text
            assert "pr.comment_ignored stranger" in thread_text
            assert "ignore previous" not in thread_text
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
