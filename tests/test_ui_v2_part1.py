"""UI v2 part 1: shell layout and kanban lanes from server-computed lanes."""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from tests._evidence import evidence_path
from treefleet.ui.server import create_app

pytestmark = pytest.mark.phase8

ROOT = Path(__file__).resolve().parents[1]


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


def _fixture(repo: Path) -> dict:
    return {
        "repo": str(repo),
        "branch": "main",
        "head": "abc1234",
        "clean": True,
        "replay": False,
        "stage": "fleet",
        "started_at": time.time() - 42,
        "run_id": "abcd1234",
        "needs_human": True,
        "awaiting": [{"id": "alpha", "title": "Parser tests", "allowed_paths": ["tests"]}],
        "tasks": [
            {
                "id": "beta",
                "title": "Notifier tests",
                "status": "running",
                "lane": "working",
                "branch": "tf/abcd1234/beta",
                "model": "composer-2.5",
                "action": "Editing tests/test_admin.py",
                "files": [{"path": "tests/test_admin.py", "additions": 12, "deletions": 2}],
                "lines": ["Editing tests/test_admin.py"],
            },
            {
                "id": "gamma",
                "title": "Admin",
                "status": "failed",
                "lane": "needs",
                "verdict": "reject",
                "reason": "no new diff",
                "error": "worker made no changes",
                "branch": "tf/abcd1234/gamma",
                "round": 1,
            },
            {
                "id": "delta",
                "title": "Docs",
                "status": "done",
                "lane": "review",
                "verdict": "approve",
                "branch": "tf/abcd1234/delta",
                "model": "composer-2.5-fast",
            },
            {
                "id": "epsilon",
                "title": "Ready task",
                "status": "done",
                "lane": "ready",
                "verdict": "approve",
                "branch": "tf/abcd1234/epsilon",
                "model": "composer-2.5",
                "files": [{"path": "README.md", "additions": 1, "deletions": 0}],
            },
        ],
        "worktrees": [],
        "events": [],
        "report": None,
    }


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


def test_static_tokens_and_board_assets_are_served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", models=[])
    client = TestClient(app, base_url="http://127.0.0.1")
    tokens = client.get("/static/css/tokens.css")
    assert tokens.status_code == 200
    assert "#07090c" in tokens.text
    assert "#4c8dff" in tokens.text
    board_css = client.get("/static/css/board.css")
    assert board_css.status_code == 200
    js = client.get("/static/js/board.js")
    assert js.status_code == 200
    assert "export" in js.text or "kanban" in js.text.lower()


def test_shell_and_kanban_layout_with_screenshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    fixture = _fixture(repo)
    app = create_app(
        repo,
        root=ROOT,
        token="test-token",
        fixture=fixture,
        models=[{"id": "composer-2.5", "label": "Composer 2.5"}],
    )
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.get_by_role("navigation", name="Sidebar").wait_for()
            page.get_by_placeholder("Search").wait_for()
            assert page.get_by_text("Ctrl+K").count() >= 1
            page.get_by_role("button", name="New task").wait_for()
            page.get_by_role("tab", name="Kanban").wait_for()
            page.get_by_role("tab", name="Orchestrator").wait_for()
            page.locator("#bell").wait_for()
            page.locator("#attention-badge").wait_for()
            for name in ("Working", "Needs you", "In review", "Ready to merge"):
                page.get_by_role("heading", name=name).wait_for()
            working = page.locator("#lane-working")
            assert "Editing tests/test_admin.py" in working.inner_text()
            assert "tf/abcd1234/beta" in working.inner_text()
            needs = page.locator("#lane-needs")
            assert "No new changes since last review" in needs.inner_text()
            ready = page.locator("#lane-ready")
            ready.get_by_role("button", name="Copy merge command", exact=True).wait_for()
            page.screenshot(path=str(evidence_path("phase-8/ui-v2/part1-desktop-1440.png")), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(200)
            page.screenshot(path=str(evidence_path("phase-8/ui-v2/part1-mobile-390.png")), full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
