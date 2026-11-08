"""Buttons keep their DOM node across a board refresh, so a click can finish."""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

from treefleet.contracts import Plan
from treefleet.ui.server import Approval, create_app

pytestmark = pytest.mark.phase8

ROOT = Path(__file__).resolve().parents[1]
GOAL = "Add unit tests for the two modules in this demo."


def _git(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, shell=False)
    subprocess.run(["git", "config", "user.email", "t@local"], cwd=path, check=True, capture_output=True, shell=False)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True, capture_output=True, shell=False)
    (path / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True, shell=False)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True, shell=False)


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")


def _plan() -> Plan:
    return Plan.model_validate(
        {
            "goal": GOAL,
            "rationale": "Two isolated test tasks.",
            "tasks": [
                {
                    "id": "alpha",
                    "title": "Add notifier tests",
                    "instructions": "Add unit tests for the notifier module.",
                    "allowed_paths": ["tests/test_notifier.py"],
                }
            ],
        }
    )


def _ready_fixture() -> dict[str, object]:
    return {
        "repo": "demo",
        "branch": "demo-base",
        "head": "abc",
        "clean": True,
        "stage": "review",
        "run_id": "abcd1234",
        "goal": GOAL,
        "awaiting": None,
        "events": [],
        "report": None,
        "tasks": [
            {
                "id": "alpha",
                "title": "Alpha",
                "lane": "ready",
                "status": "done",
                "verdict": "approve",
                "branch": "tf/abcd1234/alpha",
                "model": "composer-2.5",
                "lines": ["edited README.md"],
                "checks": [],
            }
        ],
    }


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


def test_buttons_survive_refresh_and_approve_click_spans_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", runs=tmp_path / "runs", models=[])
    board = app.state.board
    holder: dict[str, Approval | None] = {"gate": None}

    def arm() -> Approval:
        previous = holder["gate"]
        if previous is not None and previous.waiting():
            previous.finish(False)
        fresh = Approval()
        threading.Thread(target=fresh, args=(_plan(),), daemon=True).start()
        deadline = time.time() + 2
        while not fresh.waiting() and time.time() < deadline:
            time.sleep(0.01)
        assert fresh.waiting()
        board.approvals["abcd1234"] = fresh
        board.started["abcd1234"] = time.time()
        holder["gate"] = fresh
        return fresh

    arm()
    server, thread, port = _serve(app)
    sent: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.on("request", lambda req: sent.append(req.url) if req.url.endswith("/approve") else None)
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            for width in (1440, 390):
                arm()
                page.reload(wait_until="domcontentloaded")
                page.get_by_text("Plan ready").wait_for(timeout=5000)
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 844})
                if width < 500:
                    page.locator('.mobile-lane-tabs button[data-lane="needs"]').click()
                page.evaluate(
                    """() => {
                      window.__nodes = {
                        approve: document.querySelector('#needs .btn.primary'),
                        cancel: document.querySelector('#needs .btn.ghost'),
                        close: document.getElementById('drawer-close'),
                      };
                    }"""
                )
                page.wait_for_timeout(1300)
                same = page.evaluate(
                    """() => ({
                      approve: document.querySelector('#needs .btn.primary') === window.__nodes.approve,
                      cancel: document.querySelector('#needs .btn.ghost') === window.__nodes.cancel,
                      close: document.getElementById('drawer-close') === window.__nodes.close,
                    })"""
                )
                assert same == {"approve": True, "cancel": True, "close": True}, same
                sent.clear()
                box = page.get_by_role("button", name="Approve plan").bounding_box()
                assert box is not None
                page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                page.mouse.down()
                page.wait_for_timeout(1300)
                page.mouse.up()
                page.wait_for_timeout(300)
                assert any(url.endswith("/approve") for url in sent), sent
            browser.close()
    finally:
        current = holder["gate"]
        if current is not None and current.waiting():
            current.finish(False)
        server.should_exit = True
        thread.join(timeout=5)

    ready = create_app(repo, root=ROOT, token="test-token", fixture=_ready_fixture(), models=[])
    ready_server, ready_thread, ready_port = _serve(ready)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{ready_port}/", wait_until="domcontentloaded")
            for width in (1440, 390):
                page.set_viewport_size({"width": width, "height": 900 if width > 400 else 844})
                if width < 500:
                    page.locator('.mobile-lane-tabs button[data-lane="ready"]').click()
                page.locator(".card", has_text="Alpha").click()
                page.get_by_role("button", name="Pause").wait_for()
                page.evaluate(
                    """() => {
                      const buttons = [...document.querySelectorAll('#live .live-toolbar button')];
                      window.__nodes = {
                        merge: document.querySelector('[data-merge]'),
                        pause: buttons[0],
                        copy: buttons[1],
                      };
                    }"""
                )
                page.wait_for_timeout(1300)
                same = page.evaluate(
                    """() => {
                      const buttons = [...document.querySelectorAll('#live .live-toolbar button')];
                      return {
                        merge: document.querySelector('[data-merge]') === window.__nodes.merge,
                        pause: buttons[0] === window.__nodes.pause,
                        copy: buttons[1] === window.__nodes.copy,
                      };
                    }"""
                )
                assert same == {"merge": True, "pause": True, "copy": True}, same
            browser.close()
    finally:
        ready_server.should_exit = True
        ready_thread.join(timeout=5)
