"""The git graph follows the run's branches, including a replay of fea7ef2e."""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI

from treefleet.ui.server import create_app

pytestmark = pytest.mark.phase8

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "tests" / "fixtures" / "fea7ef2e-review.jsonl"


def _git(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@local"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


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


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")


def _labels(page: Any) -> list[str]:
    page.locator("#git-graph .graph-label[data-task-id]").nth(1).wait_for(timeout=8000)
    return page.locator("#git-graph .graph-label[data-task-id]").all_inner_texts()


def test_review_replay_shows_two_task_rows_and_no_integrated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", runs=tmp_path / "runs", replay=REVIEW, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            labels = _labels(page)
            assert sorted(labels) == ["tf/fea7ef2e/test-admin", "tf/fea7ef2e/test-notifications"]
            assert page.locator("[data-graph-integrated]").count() == 0
            colors = page.locator("#git-graph .graph-row:has([data-task-id]) .graph-dot").evaluate_all(
                "els => els.map((el) => getComputedStyle(el).backgroundColor)"
            )
            assert len(colors) == 2
            assert colors[0] == colors[1]
            assert colors[0] not in {"", "rgba(0, 0, 0, 0)", "transparent"}
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_integrate_appends_the_integrated_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    recorded = tmp_path / "events.jsonl"
    lines = REVIEW.read_text(encoding="utf-8").splitlines()
    extra = {
        "run_id": "fea7ef2e",
        "seq": 99,
        "ts": "2026-11-08T10:28:54Z",
        "source": "orchestrator",
        "type": "phase",
        "payload": {"name": "integrate", "duration_s": 1},
    }
    recorded.write_text("\n".join([*lines, json.dumps(extra)]) + "\n", encoding="utf-8")
    app = create_app(repo, root=ROOT, token="test-token", runs=tmp_path / "runs", replay=recorded, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            page.locator("[data-graph-integrated]").wait_for(timeout=8000)
            assert page.locator("[data-graph-integrated]").inner_text() == "tf/fea7ef2e/integrated"
            assert page.locator("#git-graph .graph-label[data-task-id]").count() == 2
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
