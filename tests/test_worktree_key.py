"""A task's tabs follow tf/<run_id>/<task_id>, not another run with the same task name."""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

from treefleet.ui.server import create_app, rows_for_run

pytestmark = pytest.mark.phase8

ROOT = Path(__file__).resolve().parents[1]


def test_rows_for_run_keeps_only_that_prefix() -> None:
    rows = [
        {"branch": "tf/aaa/alpha", "path": "C:/wt/aaa/alpha"},
        {"branch": "tf/bbb/alpha", "path": "C:/wt/bbb/alpha"},
        {"branch": "tf/aaaa/alpha", "path": "C:/wt/aaaa/alpha"},
    ]
    assert [row["branch"] for row in rows_for_run(rows, "aaa")] == ["tf/aaa/alpha"]


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
def test_tabs_stay_on_the_selected_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        "stage": "fleet",
        "run_id": "aaa",
        "started_at": time.time(),
        "awaiting": None,
        "events": [{"type": "output_line", "task_id": "alpha", "payload": {"line": "live-from-aaa"}}],
        "report": None,
        "tasks": [
            {
                "id": "alpha",
                "title": "Alpha",
                "lane": "working",
                "status": "running",
                "branch": "tf/aaa/alpha",
                "model": "composer-2.5",
                "lines": ["live-from-aaa"],
                "checks": [{"name": "pytest-aaa", "passed": True, "exit_code": 0, "duration_s": 0.1}],
                "proof": {
                    "task_id": "alpha",
                    "process_cwd_matches": True,
                    "toplevel_matches": True,
                    "registered_in_worktree_list": True,
                    "canary_absent": True,
                    "changes_within_allowed_paths": True,
                },
            }
        ],
        "worktrees": [
            {
                "branch": "tf/bbb/alpha",
                "path": "C:/wt/bbb/alpha",
                "files": [{"path": "other.py", "additions": 3, "deletions": 1}],
                "committed": [{"path": "other.py", "additions": 3, "deletions": 1}],
            },
            {
                "branch": "tf/aaa/alpha",
                "path": "C:/wt/aaa/alpha",
                "files": [{"path": "a.py", "additions": 10, "deletions": 0}],
                "committed": [{"path": "a.py", "additions": 10, "deletions": 0}],
            },
        ],
    }
    app = create_app(repo, root=ROOT, token="test-token", fixture=fixture, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            diffs: list[str] = []
            page.on("request", lambda req: diffs.append(req.url) if "/api/diff" in req.url else None)
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            card = page.locator(".card", has_text="Alpha")
            card.wait_for()
            assert card.locator(".chip.lines").inner_text() == "+10 -0"
            assert "other.py" not in card.inner_text()
            labels = page.locator("#git-graph .graph-label").all_inner_texts()
            assert any(label == "tf/aaa/alpha" for label in labels)
            assert all("tf/bbb/" not in label for label in labels)
            page.get_by_role("tab", name="Live output").click()
            assert "live-from-aaa" in page.locator("#live").inner_text()
            page.get_by_role("tab", name="Checks").click()
            assert "pytest-aaa" in page.locator("#checks").inner_text()
            page.get_by_role("tab", name="Proof").click()
            assert "other.py" not in page.locator("#proofpane").inner_text()
            page.get_by_role("tab", name="Diff").click()
            page.wait_for_timeout(400)
            assert any("C%3A%2Fwt%2Faaa%2Falpha" in url or "C:/wt/aaa/alpha" in url for url in diffs)
            assert all("bbb" not in url for url in diffs)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
