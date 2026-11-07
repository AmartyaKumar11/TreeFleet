"""UI v2 part 2b: kanban lane layout, drawer empty state, approve → running."""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from treefleet.ui.server import create_app

pytestmark = pytest.mark.phase8

ROOT = Path(__file__).resolve().parents[1]
SHOTS = ROOT / "evidence" / "phase-8" / "ui-v2"
GOAL = "Add parser edge-case tests, document every config option, and fix notifier lint."


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")


def _git(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@local"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _serve(app) -> tuple[uvicorn.Server, threading.Thread, int]:
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
    return server, thread, port


def _task(i: int, lane: str, **extra: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": f"{lane[0]}{i}",
        "title": f"{lane.title()} task {i} with a longer title for wrap checks",
        "status": "running" if lane == "working" else "done",
        "lane": lane,
        "branch": f"tf/abcd1234/{lane}-task-{i}-long-branch-name",
        "model": "composer-2.5",
    }
    if lane == "working":
        body["action"] = f"Editing tests/test_{i}.py"
    if lane == "needs":
        body["status"] = "failed"
        body["verdict"] = "reject"
        body["reason"] = "no new diff"
    if lane == "review":
        body["verdict"] = "approve"
    if lane == "ready":
        body["verdict"] = "approve"
        body["files"] = [{"path": "README.md", "additions": 1, "deletions": 0}]
    body.update(extra)
    return body


def _sixteen(repo: Path) -> dict:
    tasks = []
    for lane in ("working", "needs", "review", "ready"):
        for i in range(1, 5):
            tasks.append(_task(i, lane))
    return {
        "repo": str(repo),
        "branch": "main",
        "head": "abc1234",
        "clean": True,
        "goal": GOAL,
        "worker_model": "router",
        "stage": "fleet",
        "started_at": time.time() - 90,
        "run_id": "abcd1234",
        "needs_human": True,
        "awaiting": None,
        "tasks": tasks,
        "worktrees": [],
        "events": [],
        "report": None,
    }


def _four(repo: Path) -> dict:
    return {
        "repo": str(repo),
        "branch": "main",
        "head": "abc1234",
        "clean": True,
        "goal": GOAL,
        "worker_model": "composer-2.5",
        "stage": "fleet",
        "started_at": time.time() - 42,
        "run_id": "abcd1234",
        "needs_human": False,
        "tasks": [
            _task(1, "working"),
            _task(1, "needs"),
            _task(1, "review"),
            _task(1, "ready"),
        ],
        "worktrees": [],
        "events": [],
        "report": None,
    }


def _awaiting(repo: Path) -> dict:
    return {
        "repo": str(repo),
        "branch": "main",
        "head": "abc1234",
        "clean": True,
        "goal": GOAL,
        "worker_model": "router",
        "stage": "approve",
        "started_at": time.time() - 60,
        "run_id": "abcd1234",
        "needs_human": True,
        "awaiting": [
            {"id": "alpha", "title": "Parser tests", "allowed_paths": ["tests/"]},
            {"id": "beta", "title": "Notifier lint", "allowed_paths": ["src/notifier/"]},
        ],
        "tasks": [],
        "worktrees": [],
        "events": [
            {
                "type": "guard.decision",
                "seq": 1,
                "payload": {
                    "stage": "input",
                    "allowed": True,
                    "reasons": ["length ok"],
                    "scores": {"prompt_injection": 0.02},
                },
            }
        ],
        "explored": ["a.py", "b.py", "c.py"],
        "report": None,
    }


def _finished(repo: Path) -> dict:
    return {
        "repo": str(repo),
        "branch": "main",
        "head": "abc1234",
        "clean": True,
        "goal": GOAL,
        "worker_model": "router",
        "stage": "done",
        "started_at": time.time() - 200,
        "finished_at": time.time(),
        "run_id": "abcd1234",
        "needs_human": False,
        "tasks": [
            {
                "id": "alpha",
                "title": "Parser tests",
                "lane": "ready",
                "status": "done",
                "verdict": "approve",
                "branch": "tf/abcd1234/alpha",
                "model": "composer-2.5",
            },
            {
                "id": "gamma",
                "title": "Config docs",
                "lane": "needs",
                "status": "failed",
                "verdict": "reject",
                "reason": "merge conflict",
                "conflict": True,
                "branch": "tf/abcd1234/gamma",
                "model": "composer-2.5",
            },
        ],
        "events": [
            {
                "type": "guard.decision",
                "seq": 1,
                "payload": {"stage": "input", "allowed": True, "reasons": [], "scores": {}},
            }
        ],
        "worktrees": [],
        "report": {
            "goal": GOAL,
            "integration": {
                "branch": "tf/abcd1234/integrated",
                "merged": ["alpha"],
                "excluded": ["gamma: merge conflict"],
                "checks": [{"name": "pytest", "passed": True}],
            },
            "verdicts": [
                {"task_id": "alpha", "verdict": "approve", "reasons": []},
                {"task_id": "gamma", "verdict": "reject", "reasons": ["merge conflict"]},
            ],
            "metrics": {
                "wall_seconds": 126.2,
                "speedup": 1.87,
                "fleet_speedup": 2.4,
                "total_usd": 0.042,
                "costs": [],
            },
        },
    }


def test_kanban_equal_lanes_sticky_headers_and_drawer_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    fixture = _sixteen(repo)
    app = create_app(repo, root=ROOT, token="test-token", fixture=fixture, models=[])
    server, thread, port = _serve(app)
    SHOTS.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            # clear auto-selected working task so drawer shows empty summary
            page.evaluate(
                """() => {
                  document.getElementById('task-title').textContent = 'No task selected';
                  window.__tfClearSelection && window.__tfClearSelection();
                }"""
            )
            page.keyboard.press("Escape")
            page.wait_for_timeout(100)
            metrics = page.evaluate(
                """() => {
                  const lanes = [...document.querySelectorAll('.lanes > .lane')];
                  const widths = lanes.map((lane) => lane.getBoundingClientRect().width);
                  const cards = [...document.querySelectorAll('.lane .card')].map((card) => {
                    const lane = card.closest('.lane');
                    // clientWidth is the lane content box (cards are width:100% of that)
                    return {
                      card: card.getBoundingClientRect().width,
                      lane: lane.clientWidth,
                    };
                  });
                  const headers = lanes.map((lane) => {
                    const h = lane.querySelector('h2');
                    const stack = lane.querySelector('.stack');
                    stack.scrollTop = 120;
                    const hr = h.getBoundingClientRect();
                    const lr = lane.getBoundingClientRect();
                    return { visible: hr.bottom > lr.top && hr.top < lr.bottom, top: hr.top, laneTop: lr.top };
                  });
                  return { widths, cards, headers, titles: lanes.map((l) => l.querySelector('h2').innerText) };
                }"""
            )
            assert len(metrics["widths"]) == 4
            assert max(metrics["widths"]) - min(metrics["widths"]) < 8
            assert all(item["card"] >= item["lane"] - 2 for item in metrics["cards"])
            assert all(item["visible"] for item in metrics["headers"])
            for name in ("Working", "Needs you", "In review", "Ready to merge"):
                assert any(name in title for title in metrics["titles"])
            drawer = page.locator(".drawer")
            assert "Select a task" in drawer.inner_text() or "select a task" in drawer.inner_text().lower()
            assert "Working" in drawer.inner_text()
            page.screenshot(path=str(SHOTS / "part2b-kanban-16-1440.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(200)
            page.screenshot(path=str(SHOTS / "part2b-kanban-16-390.png"), full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_kanban_four_task_stub_screenshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", fixture=_four(repo), models=[])
    server, thread, port = _serve(app)
    SHOTS.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.screenshot(path=str(SHOTS / "part2b-kanban-4-1440.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(200)
            page.screenshot(path=str(SHOTS / "part2b-kanban-4-390.png"), full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_approve_moves_to_running_and_shared_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", fixture=_awaiting(repo), models=[])
    server, thread, port = _serve(app)
    SHOTS.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            assert "Awaiting approval" in page.locator("#run-status").inner_text()
            pill = page.locator("#attention")
            assert pill.is_visible()
            assert pill.inner_text().strip() not in {"", "Needs you"}
            page.get_by_role("tab", name="Orchestrator").click()
            assert "Smart routing" in page.locator("#orch-thread").inner_text()
            page.get_by_role("button", name="Approve plan").first.click()
            page.wait_for_timeout(150)
            assert "Running" in page.locator("#run-status").inner_text()
            assert "starting" in page.locator("#orch-thread").inner_text().lower()
            elapsed = page.locator("#elapsed").inner_text()
            strip = page.locator("#run-status").inner_text()
            assert elapsed in strip
            page.screenshot(path=str(SHOTS / "part2b-awaiting-1440.png"), full_page=True)
            # reload for clean awaiting shot already taken after approve — take running-ish
            page.screenshot(path=str(SHOTS / "part2b-running-1440.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(200)
            page.screenshot(path=str(SHOTS / "part2b-running-390.png"), full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_thread_conflict_rows_and_finished_screenshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    # fresh awaiting shot without approve
    app = create_app(repo, root=ROOT, token="test-token", fixture=_awaiting(repo), models=[])
    server, thread, port = _serve(app)
    SHOTS.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.get_by_role("tab", name="Orchestrator").click()
            page.screenshot(path=str(SHOTS / "part2b-awaiting-1440.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(150)
            page.screenshot(path=str(SHOTS / "part2b-awaiting-390.png"), full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    app2 = create_app(repo, root=ROOT, token="test-token", fixture=_finished(repo), models=[])
    server2, thread2, port2 = _serve(app2)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port2}/", wait_until="networkidle")
            page.get_by_role("tab", name="Orchestrator").click()
            thread_el = page.locator("#orch-thread")
            assert "conflict, excluded" in thread_el.inner_text().lower()
            assert "reject · merge conflict" not in thread_el.inner_text().lower()
            copy = page.get_by_role("button", name="Copy merge command", exact=True)
            copy.click()
            page.wait_for_timeout(100)
            assert "Copied" in page.locator(".summary-card").inner_text() or copy.inner_text() == "Copied"
            jump = page.locator("#jump-latest")
            box = jump.bounding_box()
            composer = page.locator(".orch-composer").bounding_box()
            if box and composer:
                assert box["y"] + box["height"] <= composer["y"] + 1
            page.screenshot(path=str(SHOTS / "part2b-finished-1440.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(150)
            page.screenshot(path=str(SHOTS / "part2b-finished-390.png"), full_page=True)
            browser.close()
    finally:
        server2.should_exit = True
        thread2.join(timeout=5)
