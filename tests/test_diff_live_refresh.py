"""Live diff refresh and asset Cache-Control (phase 15.B)."""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi.testclient import TestClient

from treefleet.ui.server import create_app

pytestmark = pytest.mark.phase8

ROOT = Path(__file__).resolve().parents[1]


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")


def _git(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@local"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("base\n", encoding="utf-8", newline="\n")
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


def _worktree(repo: Path, branch: str, folder: Path) -> str:
    folder.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(folder)],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=folder,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return head


def _branch(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _mutable_board(repo: Path, wt_a: Path, wt_b: Path, head_a: str, head_b: str) -> dict[str, Any]:
    return {
        "repo": str(repo),
        "branch": _branch(repo),
        "head": head_a,
        "clean": True,
        "replay": False,
        "stage": "fleet",
        "started_at": time.time() - 10,
        "run_id": "abcd1234",
        "needs_human": False,
        "awaiting": None,
        "tasks": [
            {
                "id": "alpha",
                "title": "Alpha task",
                "status": "running",
                "lane": "working",
                "branch": "tf/abcd1234/alpha",
                "lines": ["working"],
            },
            {
                "id": "beta",
                "title": "Beta task",
                "status": "running",
                "lane": "working",
                "branch": "tf/abcd1234/beta",
                "lines": ["working"],
            },
        ],
        "worktrees": [
            {
                "path": str(wt_a),
                "branch": "tf/abcd1234/alpha",
                "changed": 0,
                "files": [],
                "committed": [],
                "head": head_a,
            },
            {
                "path": str(wt_b),
                "branch": "tf/abcd1234/beta",
                "changed": 0,
                "files": [],
                "committed": [],
                "head": head_b,
            },
        ],
        "events": [],
        "report": None,
        "runs": [{"id": "abcd1234"}],
    }


def test_cache_control_no_cache_and_etag_revalidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", models=[])
    client = TestClient(app, base_url="http://127.0.0.1")
    home = client.get("/")
    assert home.status_code == 200
    assert home.headers.get("cache-control") == "no-cache"
    tokens = client.get("/static/css/tokens.css")
    assert tokens.status_code == 200
    assert tokens.headers.get("cache-control") == "no-cache"
    etag = tokens.headers.get("etag")
    assert etag
    again = client.get("/static/css/tokens.css", headers={"If-None-Match": etag})
    assert again.status_code == 304
    assert again.headers.get("cache-control") == "no-cache"


@pytest.mark.ui
def test_diff_pane_refreshes_on_signal_without_reload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    wt_a = tmp_path / "wt-alpha"
    wt_b = tmp_path / "wt-beta"
    head_a = _worktree(repo, "tf/abcd1234/alpha", wt_a)
    head_b = _worktree(repo, "tf/abcd1234/beta", wt_b)
    (wt_a / "note.txt").write_text("one\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "note.txt"], cwd=wt_a, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "note"], cwd=wt_a, check=True, capture_output=True)
    head_a = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=wt_a,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (wt_a / "note.txt").write_text("one\npending\n", encoding="utf-8", newline="\n")
    fixture = _mutable_board(repo, wt_a, wt_b, head_a, head_b)
    assert isinstance(fixture["worktrees"], list)
    fixture["worktrees"][0] = {
        "path": str(wt_a),
        "branch": "tf/abcd1234/alpha",
        "changed": 1,
        "files": [{"path": "note.txt", "additions": 1, "deletions": 0}],
        "committed": [{"path": "note.txt", "additions": 1, "deletions": 0}],
        "head": head_a,
    }
    app = create_app(repo, root=ROOT, token="test-token", fixture=fixture, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            diff_urls: list[str] = []

            def on_request(request) -> None:  # type: ignore[no-untyped-def]
                if "/api/diff" in request.url:
                    diff_urls.append(request.url)

            page.on("request", on_request)
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.locator(".card", has_text="Alpha task").first.click()
            page.get_by_role("tab", name="Diff").click()
            page.wait_for_function("() => (document.querySelector('#diff')?.innerText || '').includes('pending')")
            before = len(diff_urls)
            assert before >= 1

            (wt_a / "note.txt").write_text("one\npending\ntwo\n" + ("x\n" * 40), encoding="utf-8", newline="\n")
            fixture["worktrees"][0] = {
                "path": str(wt_a),
                "branch": "tf/abcd1234/alpha",
                "changed": 1,
                "files": [{"path": "note.txt", "additions": 42, "deletions": 0}],
                "committed": [{"path": "note.txt", "additions": 1, "deletions": 0}],
                "head": head_a,
            }
            page.locator("#diff").evaluate(
                """el => {
                  const node = el.querySelector('.diff-view') || el.querySelector('.diff-body');
                  if (node) node.scrollTop = 40;
                }"""
            )
            deadline = time.time() + 5
            while time.time() < deadline and len(diff_urls) < before + 1:
                page.wait_for_timeout(100)
            after = len(diff_urls)
            assert after == before + 1
            page.wait_for_function("() => (document.querySelector('#diff')?.innerText || '').includes('two')")
            page.wait_for_timeout(1200)
            assert len(diff_urls) == after
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.ui
def test_diff_ignores_stale_response_after_task_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    wt_a = tmp_path / "wt-alpha"
    wt_b = tmp_path / "wt-beta"
    head_a = _worktree(repo, "tf/abcd1234/alpha", wt_a)
    head_b = _worktree(repo, "tf/abcd1234/beta", wt_b)
    (wt_a / "alpha.txt").write_text("alpha-only\n", encoding="utf-8", newline="\n")
    (wt_b / "beta.txt").write_text("beta-only\n", encoding="utf-8", newline="\n")
    fixture = _mutable_board(repo, wt_a, wt_b, head_a, head_b)
    fixture["worktrees"] = [
        {
            "path": str(wt_a),
            "branch": "tf/abcd1234/alpha",
            "changed": 1,
            "files": [{"path": "alpha.txt", "additions": 1, "deletions": 0}],
            "committed": [],
            "head": head_a,
        },
        {
            "path": str(wt_b),
            "branch": "tf/abcd1234/beta",
            "changed": 1,
            "files": [{"path": "beta.txt", "additions": 1, "deletions": 0}],
            "committed": [],
            "head": head_b,
        },
    ]
    app = create_app(repo, root=ROOT, token="test-token", fixture=fixture, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            held: list[object] = []
            seen = {"n": 0}

            def handle(route) -> None:  # type: ignore[no-untyped-def]
                if "/api/diff" not in route.request.url:
                    route.fallback()
                    return
                seen["n"] += 1
                if seen["n"] == 1:
                    held.append(route)
                    return
                route.fallback()

            page.route("**/api/diff*", handle)
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.locator(".card", has_text="Alpha task").first.click()
            page.get_by_role("tab", name="Diff").click()
            deadline = time.time() + 5
            while time.time() < deadline and not held:
                page.wait_for_timeout(50)
            assert held
            page.locator(".card", has_text="Beta task").first.click()
            page.get_by_role("tab", name="Diff").click()
            page.wait_for_function(
                "() => (document.querySelector('#diff')?.innerText || '').toLowerCase().includes('beta')"
            )
            assert "beta" in page.locator("#diff").inner_text().lower()
            held[0].fulfill(  # type: ignore[attr-defined]
                status=200,
                content_type="application/json",
                body='{"diff":"+alpha-stale\\n","files":[{"path":"alpha.txt","additions":1,"deletions":0}]}',
            )
            page.wait_for_timeout(400)
            text = page.locator("#diff").inner_text().lower()
            assert "alpha-stale" not in text
            assert "beta" in text
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.ui
def test_diff_keeps_last_good_on_fetch_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    wt_a = tmp_path / "wt-alpha"
    wt_b = tmp_path / "wt-beta"
    head_a = _worktree(repo, "tf/abcd1234/alpha", wt_a)
    head_b = _worktree(repo, "tf/abcd1234/beta", wt_b)
    (wt_a / "note.txt").write_text("keep-me\n", encoding="utf-8", newline="\n")
    fixture = _mutable_board(repo, wt_a, wt_b, head_a, head_b)
    fixture["worktrees"][0] = {
        "path": str(wt_a),
        "branch": "tf/abcd1234/alpha",
        "changed": 1,
        "files": [{"path": "note.txt", "additions": 1, "deletions": 0}],
        "committed": [],
        "head": head_a,
    }
    app = create_app(repo, root=ROOT, token="test-token", fixture=fixture, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            fail_next = {"on": False}

            def handle(route) -> None:  # type: ignore[no-untyped-def]
                if "/api/diff" not in route.request.url:
                    route.fallback()
                    return
                if fail_next["on"]:
                    route.fulfill(status=500, body="nope")
                    return
                route.fallback()

            page.route("**/api/diff*", handle)
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.locator(".card", has_text="Alpha task").first.click()
            page.get_by_role("tab", name="Diff").click()
            page.wait_for_function("() => (document.querySelector('#diff')?.innerText || '').includes('note')")
            good = page.locator("#diff").inner_text()
            assert "note" in good.lower()
            fail_next["on"] = True
            fixture["worktrees"][0] = {
                "path": str(wt_a),
                "branch": "tf/abcd1234/alpha",
                "changed": 1,
                "files": [{"path": "note.txt", "additions": 2, "deletions": 0}],
                "committed": [],
                "head": head_a,
            }
            page.wait_for_timeout(1300)
            text = page.locator("#diff").inner_text()
            assert "note" in text.lower()
            assert "could not refresh" in text.lower()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
