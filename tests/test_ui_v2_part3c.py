"""UI v2 part 3c screenshots and polish checks."""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from tests._evidence import evidence_path
from treefleet.ui.server import create_app

pytestmark = pytest.mark.phase8

ROOT = Path(__file__).resolve().parents[1]
GOAL = "Add parser edge-case tests."


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


def _branches(repo: Path, count: int) -> dict[str, object]:
    lanes = ("working", "needs", "review", "ready")
    tasks = []
    for index in range(1, count + 1):
        lane = lanes[index % 4]
        tasks.append(
            {
                "id": f"t{index}",
                "title": f"Task {index}",
                "lane": lane,
                "status": "failed" if lane == "needs" else "done",
                "branch": f"tf/abcd1234/task-{index}-with-a-long-branch-name",
                "model": "composer-2.5",
                "verdict": "reject" if lane == "needs" else "approve",
            }
        )
    return {
        "repo": str(repo),
        "branch": "main",
        "head": "abc1234",
        "clean": True,
        "goal": GOAL,
        "worker_model": "router",
        "stage": "fleet",
        "started_at": time.time() - 30,
        "run_id": "abcd1234",
        "tasks": tasks,
        "worktrees": [],
        "events": [],
        "report": {
            "integration": {"branch": "tf/abcd1234/integrated", "base": "abc1234", "merged": [], "excluded": []}
        },
    }


def _board(repo: Path) -> dict[str, object]:
    diff = (
        "diff --git a/src/a.py b/src/a.py\nindex 111..222 100644\n--- a/src/a.py\n+++ b/src/a.py\n"
        "@@ -1 +1,2 @@\n-old\n+alpha line that is intentionally wider than the diff pane so it must scroll\n"
    )
    return {
        "repo": str(repo),
        "branch": "main",
        "head": "abc1234",
        "clean": True,
        "goal": GOAL,
        "worker_model": "router",
        "stage": "fleet",
        "started_at": time.time() - 40,
        "run_id": "abcd1234",
        "tasks": [
            {
                "id": "alpha",
                "title": "Parser tests",
                "lane": "working",
                "status": "running",
                "branch": "tf/abcd1234/alpha",
                "model": "composer-2.5",
                "action": "Editing tests",
                "diff": diff,
                "diff_files": [{"path": "src/a.py", "additions": 1, "deletions": 1}],
            },
            {
                "id": "need",
                "title": "Needs review",
                "lane": "needs",
                "status": "needs_review",
                "verdict": "needs_review",
                "reason": "no new diff",
                "branch": "tf/abcd1234/need",
                "model": "composer-2.5",
            },
            {
                "id": "ready",
                "title": "Ready task",
                "lane": "ready",
                "status": "done",
                "verdict": "approve",
                "branch": "tf/abcd1234/ready",
                "model": "composer-2.5",
            },
        ],
        "worktrees": [],
        "events": [],
        "explored": ["a.py"],
        "awaiting": None,
        "report": {"integration": {"branch": "tf/abcd1234/integrated", "base": "abc1234", "merged": ["ready"]}},
    }


def _plan(repo: Path) -> dict[str, object]:
    return {
        "repo": str(repo),
        "branch": "main",
        "head": "abc1234",
        "clean": True,
        "goal": GOAL,
        "worker_model": "router",
        "stage": "approve",
        "started_at": time.time() - 20,
        "run_id": "abcd1234",
        "awaiting": [
            {"id": "alpha", "title": "Parser tests", "allowed_paths": ["tests/"]},
            {"id": "beta", "title": "Admin tests", "allowed_paths": ["src/"]},
        ],
        "tasks": [],
        "worktrees": [],
        "events": [{"type": "guard.decision", "seq": 1, "payload": {"stage": "input", "allowed": True, "reasons": []}}],
        "explored": ["a.py"],
        "report": None,
    }


def _shot(page, name: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_timeout(150)
    page.screenshot(path=str(evidence_path(f"phase-8/ui-v2/3c/{name}-1440.png")), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(150)
    page.screenshot(path=str(evidence_path(f"phase-8/ui-v2/3c/{name}-390.png")), full_page=True)


@pytest.mark.ui
def test_part3c_graph_ready_diff_and_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)

    def open_graph(count: int):
        app = create_app(repo, root=ROOT, token="test-token", fixture=_branches(repo, count), models=[])
        return _serve(app)

    server, thread, port = open_graph(6)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            page.wait_for_timeout(400)
            wide = page.locator("#git-graph .graph-list").bounding_box()
            assert page.locator("#git-graph .graph-label[data-task-id]").count() == 6
            assert page.locator("[data-graph-base]").count() >= 1
            assert page.locator("[data-graph-integrated]").count() >= 1
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(150)
            narrow = page.locator("#git-graph .graph-list").bounding_box()
            assert wide and narrow
            assert abs(wide["width"] - narrow["width"]) < 2
            page.set_viewport_size({"width": 1440, "height": 900})
            _shot(page, "graph6")
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    server, thread, port = open_graph(16)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            page.wait_for_timeout(400)
            assert page.locator("#git-graph .graph-label[data-task-id]").count() == 8
            assert "+8 more" in page.locator("#git-graph").inner_text()
            _shot(page, "graph16-collapsed")
            page.locator("button[data-graph-more]").click()
            page.wait_for_timeout(100)
            assert page.locator("#git-graph .graph-label[data-task-id]").count() == 16
            _shot(page, "graph16-expanded")
            page.locator("button[data-graph-more]").click()
            page.wait_for_timeout(100)
            assert page.locator("#git-graph .graph-label[data-task-id]").count() == 8
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    app = create_app(repo, root=ROOT, token="test-token", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            page.wait_for_timeout(400)
            merge = page.locator("#ready .merge-btn")
            box = merge.evaluate(
                """el => {
                  const card = el.closest('.card');
                  const cs = getComputedStyle(card);
                  const pad = parseFloat(cs.paddingLeft) + parseFloat(cs.paddingRight);
                  const s = getComputedStyle(el);
                  return {
                    card: card.clientWidth - pad,
                    btn: el.getBoundingClientRect().width,
                    box: s.boxSizing,
                    min: s.minWidth,
                  };
                }"""
            )
            assert box["box"] == "border-box"
            assert box["min"] == "0px"
            assert abs(box["btn"] - box["card"]) < 3
            page.locator("#needs .chip.verdict").first.wait_for()
            needs = page.locator("#needs .chip.verdict").first.evaluate("el => getComputedStyle(el).color")
            ready = page.locator("#ready .chip.verdict").first.evaluate("el => getComputedStyle(el).color")
            assert needs != ready
            page.keyboard.press("Escape")
            assert page.locator("#drawer-close").is_hidden()
            page.screenshot(path=str(evidence_path("phase-8/ui-v2/3c/ready-1440.png")), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(150)
            page.screenshot(path=str(evidence_path("phase-8/ui-v2/3c/ready-390.png")), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 900})
            page.locator(".card", has_text="Parser tests").first.click()
            page.get_by_role("tab", name="Diff").click()
            page.wait_for_timeout(80)
            diff = page.locator("#diff").inner_text()
            assert "diff --git" not in diff
            assert "index " not in diff
            assert "alpha line" in diff
            wrap = page.locator(".diff-line .code").first.evaluate("el => getComputedStyle(el).whiteSpace")
            assert wrap == "pre"
            page.screenshot(path=str(evidence_path("phase-8/ui-v2/3c/diff-1440.png")), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(200)
            if not page.locator(".drawer").evaluate("el => el.classList.contains('is-open')"):
                page.locator(".card", has_text="Parser tests").first.click()
            page.get_by_role("tab", name="Diff").click()
            page.wait_for_timeout(100)
            page.screenshot(path=str(evidence_path("phase-8/ui-v2/3c/diff-390.png")), full_page=True)
            assert page.locator("#drawer-backdrop").is_visible()
            page.locator("#drawer-backdrop").click()
            page.wait_for_timeout(100)
            assert page.locator(".drawer").evaluate("el => el.classList.contains('is-open')") is False
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    app = create_app(repo, root=ROOT, token="test-token", fixture=_plan(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            page.wait_for_timeout(300)
            page.get_by_role("tab", name="Orchestrator").click()
            body = page.locator("#orch-thread").inner_text()
            assert "Explored 1 file" in body
            assert "Explored 1 files" not in body
            assert "Routed: Smart routing" in body
            _shot(page, "thread")
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
