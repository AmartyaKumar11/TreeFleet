"""Approve-plan click on the live board server, with a stub plan and no model call."""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import pytest
import uvicorn

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


def _board(port: int) -> dict[str, object]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/board",
        headers={"Authorization": "Bearer test-token"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        body = json.load(response)
    return body if isinstance(body, dict) else {}


def _http_approve(port: int) -> int:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/runs/abcd1234/approve",
        data=b"{}",
        method="POST",
        headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status


def test_approve_click_leaves_approve_within_five_seconds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", runs=tmp_path / "runs", models=[])
    board = app.state.board
    plan = _plan()
    holder: dict[str, Approval | None] = {"gate": None}

    def arm() -> Approval:
        previous = holder["gate"]
        if previous is not None and previous.waiting():
            previous.finish(False)
        gate = Approval()
        threading.Thread(target=gate, args=(plan,), daemon=True).start()
        deadline = time.time() + 2
        while not gate.waiting() and time.time() < deadline:
            time.sleep(0.01)
        assert gate.waiting()
        board.approvals.clear()
        board.approvals["abcd1234"] = gate
        board.started["abcd1234"] = time.time()
        holder["gate"] = gate
        return gate

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    ready = time.time() + 10
    while time.time() < ready and not server.started:
        time.sleep(0.05)
    assert server.started
    failures: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            bag: dict[str, list[str]] = {"console": [], "network": []}

            def on_console(msg) -> None:  # type: ignore[no-untyped-def]
                if msg.type == "error":
                    bag["console"].append(f"{msg.type}: {msg.text}")

            def on_response(res) -> None:  # type: ignore[no-untyped-def]
                if str(res.url).endswith("/approve"):
                    bag["network"].append(f"{res.status} {res.url}")

            page.on("console", on_console)
            page.on("response", on_response)
            for width in (1440, 390):
                for attempt in range(1, 11):
                    arm()
                    page.set_viewport_size({"width": width, "height": 900 if width > 400 else 844})
                    bag["console"] = []
                    bag["network"] = []
                    try:
                        if width < 500:
                            page.locator('.mobile-lane-tabs button[data-lane="needs"]').click()
                        page.get_by_text("Plan ready").wait_for(timeout=5000)
                        page.get_by_role("button", name="Approve plan").click()
                        deadline = time.time() + 5
                        while time.time() < deadline and _board(port).get("stage") == "approve":
                            time.sleep(0.1)
                        stage = _board(port).get("stage")
                        if stage == "approve":
                            http_status = _http_approve(port)
                            failures.append(
                                f"{width} #{attempt} click left stage={stage} http={http_status} "
                                f"network={bag['network']} console={bag['console']}"
                            )
                    except Exception as exc:
                        failures.append(
                            f"{width} #{attempt} {type(exc).__name__}: {exc} "
                            f"network={bag['network']} console={bag['console']}"
                        )
            arm()
            assert _http_approve(port) == 200
            deadline = time.time() + 5
            while time.time() < deadline and _board(port).get("stage") == "approve":
                time.sleep(0.1)
            assert _board(port).get("stage") != "approve"
            browser.close()
    finally:
        gate = holder["gate"]
        if gate is not None and gate.waiting():
            gate.finish(False)
        server.should_exit = True
        thread.join(timeout=5)
    assert failures == []


def test_immediate_approve_click_survives_the_one_second_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Click as soon as the button appears, then at half a second, one second, and two seconds."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", runs=tmp_path / "runs", models=[])
    board = app.state.board
    plan = _plan()
    holder: dict[str, Approval | None] = {"gate": None}

    def arm() -> Approval:
        previous = holder["gate"]
        if previous is not None and previous.waiting():
            previous.finish(False)
        gate = Approval()
        threading.Thread(target=gate, args=(plan,), daemon=True).start()
        deadline = time.time() + 2
        while not gate.waiting() and time.time() < deadline:
            time.sleep(0.01)
        assert gate.waiting()
        board.approvals.clear()
        board.approvals["abcd1234"] = gate
        board.started["abcd1234"] = time.time()
        holder["gate"] = gate
        return gate

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    ready = time.time() + 10
    while time.time() < ready and not server.started:
        time.sleep(0.05)
    assert server.started
    failures: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            console: list[str] = []
            page.on("console", lambda msg: console.append(f"{msg.type}: {msg.text}") if msg.type == "error" else None)
            for width, height in ((1440, 900), (390, 844)):
                page.set_viewport_size({"width": width, "height": height})
                for attempt in range(1, 31):
                    arm()
                    console.clear()
                    if width < 500:
                        page.locator('.mobile-lane-tabs button[data-lane="needs"]').click()
                    page.get_by_text("Plan ready").wait_for(timeout=5000)
                    appeared = time.time()
                    born = page.evaluate(
                        """() => {
                          const button = [...document.querySelectorAll('button')]
                            .find((el) => el.textContent.trim() === 'Approve plan');
                          if (!button) return '';
                          button.dataset.born = String(performance.now());
                          return button.dataset.born;
                        }"""
                    )
                    page.get_by_role("button", name="Approve plan").click()
                    time.sleep(0.25)
                    stage = _board(port).get("stage")
                    if stage == "approve":
                        replaced = page.evaluate(
                            """(born) => {
                              const button = [...document.querySelectorAll('button')]
                                .find((el) => el.textContent.trim() === 'Approve plan');
                              const needs = document.getElementById('needs');
                              return {
                                replaced: !button || button.dataset.born !== born,
                                needs: needs ? needs.innerText.slice(0, 240) : '',
                              };
                            }""",
                            born,
                        )
                        failures.append(
                            f"{width} #{attempt} first click left stage=approve replaced={replaced} console={console}"
                        )
                        for delay in (0.5, 1.0, 2.0):
                            remain = appeared + delay - time.time()
                            if remain > 0:
                                time.sleep(remain)
                            if page.get_by_role("button", name="Approve plan").count() == 0:
                                break
                            page.get_by_role("button", name="Approve plan").click()
                            time.sleep(0.2)
                            if _board(port).get("stage") != "approve":
                                break
            browser.close()
    finally:
        gate = holder["gate"]
        if gate is not None and gate.waiting():
            gate.finish(False)
        server.should_exit = True
        thread.join(timeout=5)
    assert failures == []
