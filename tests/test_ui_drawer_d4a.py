"""15.D4a drawer shell, live/proof/checks/cost, D3 carry-overs."""

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
XSS = '<img src=x onerror="window.__xss=1">'


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


def _board(repo: Path, **extra: object) -> dict:
    now = time.time()
    body = {
        "repo": str(repo),
        "branch": "main",
        "head": "abc1234",
        "clean": True,
        "goal": "Drawer board",
        "stage": "fleet",
        "started_at": now - 90,
        "run_id": "abcd1234",
        "needs_human": False,
        "awaiting": None,
        "tasks": [
            {
                "id": "w1",
                "title": XSS,
                "status": "running",
                "lane": "working",
                "branch": f"tf/abcd1234/{XSS}",
                "model": "composer-2.5",
                "action": "Editing board.css",
                "lines": ["reading src/a.py", "error: boom failed", "writing out.py"],
                "checks": [{"name": "pytest", "passed": False, "exit_code": 1, "output": "fail", "source": "worker"}],
                "costs": [{"component": "planner", "tokens": 1200, "usd": 0.0123}],
            }
        ],
        "events": [],
        "worktrees": [],
        "report": {},
    }
    body.update(extra)
    return body


@pytest.mark.ui
def test_drawer_closed_by_default_and_toggle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            assert page.locator("#task-drawer").is_hidden()
            widths = page.evaluate(
                """() => [...document.querySelectorAll('.lane:not(.is-rail)')].map(
                  el => el.getBoundingClientRect().width)"""
            )
            assert widths and min(widths) >= 264
            assert len(widths) == 4
            card = page.locator(".card[data-task-id='w1']")
            card.click()
            page.wait_for_timeout(100)
            assert page.locator("#task-drawer").is_visible()
            page.keyboard.press("Escape")
            page.wait_for_timeout(80)
            assert page.locator("#task-drawer").is_hidden()
            assert page.evaluate("() => document.activeElement?.dataset?.taskId") == "w1"
            card.click()
            page.wait_for_timeout(80)
            card.click()
            page.wait_for_timeout(80)
            assert page.locator("#task-drawer").is_hidden()
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.ui
def test_run_strip_one_icon_per_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    cases = [
        ("fleet", None, "", True, False, False),
        ("approve", [{"id": "p", "title": "P", "allowed_paths": []}], "", False, True, False),
        ("timed_out", None, "fleet failed", False, False, True),
    ]
    for stage, awaiting, message, spin, needs, alert in cases:
        fixture = _board(repo, stage=stage, awaiting=awaiting, message=message)
        app = create_app(repo, root=ROOT, token="t", fixture=fixture, models=[])
        server, thread, port = _serve(app)
        try:
            with sync_playwright() as p:
                page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
                page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
                assert page.locator("#run-strip-spinner").is_visible() is spin
                assert page.locator("#run-strip-needs").is_visible() is needs
                assert page.locator("#run-strip-alert").is_visible() is alert
                visible = sum(
                    [
                        page.locator("#run-strip-spinner").is_visible(),
                        page.locator("#run-strip-needs").is_visible(),
                        page.locator("#run-strip-alert").is_visible(),
                    ]
                )
                assert visible <= 1
                page.close()
        finally:
            server.should_exit = True
            thread.join(timeout=5)


@pytest.mark.ui
def test_empty_board_and_skeleton_delay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    empty = _board(repo, tasks=[])
    app = create_app(repo, root=ROOT, token="t", fixture=empty, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            assert page.locator("#board-empty").is_visible()
            assert "No tasks yet" in page.locator("#board-empty").inner_text()
            assert page.locator(".lane.is-rail").count() == 0
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    fixture = _board(repo)
    app2 = create_app(repo, root=ROOT, token="t", fixture=fixture, models=[])
    server, thread, port = _serve(app2)
    shot = Path(tmp_path) / "skeleton-during-delay.png"
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})

            held: list = []
            release = {"go": False}

            def handle(route):
                if "/api/board" in route.request.url and not release["go"]:
                    held.append(route)
                    return
                route.continue_()

            page.route("**/api/board*", handle)
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            page.wait_for_selector(".skeleton-card", timeout=5000)
            sk = page.locator(".skeleton-card")
            page.screenshot(path=str(shot), full_page=True)
            assert sk.count() >= 4
            heights = sk.evaluate_all("els => els.map(el => el.getBoundingClientRect().height)")
            assert heights and min(heights) >= 100
            release["go"] = True
            for route in held:
                route.continue_()
            page.wait_for_selector(".card[data-task-id='w1']", timeout=8000)
            assert page.locator(".skeleton-card").count() == 0
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.ui
def test_drawer_tabs_live_proof_xss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.locator(".card[data-task-id='w1']").click()
            page.wait_for_timeout(100)
            assert XSS in page.locator("#task-title").inner_text()
            assert page.locator("#task-title img, #live script").count() == 0
            page.get_by_role("tab", name="Live output").focus()
            page.keyboard.press("ArrowRight")
            assert page.locator(".drawer .tab[aria-selected='true']").inner_text() in {"Diff", "Live output"}
            page.get_by_role("tab", name="Checks").click()
            assert page.locator("#checks .detail-row.is-fail").count() >= 1
            page.get_by_role("tab", name="Cost").click()
            assert "$0.0123" in page.locator("#costpane").inner_text()
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
