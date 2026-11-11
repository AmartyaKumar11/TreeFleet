"""UI v2 part 3d: one graph control, human card text, verdict after review."""

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
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.05)
    return server, thread, port


@pytest.mark.ui
def test_part3d_cards_and_graph(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    long = "tf/abcd1234/" + "feature-branch-name-that-will-not-fit" * 2
    tasks = []
    for i in range(1, 17):
        tasks.append(
            {
                "id": f"t{i}",
                "title": f"Task {i}",
                "branch": f"{long}-{i}",
                "lane": "working" if i == 1 else "ready",
                "status": "running" if i == 1 else "done",
                "model": "composer-2.5",
            }
        )
    tasks.append(
        {
            "id": "held",
            "title": "Held",
            "branch": "tf/abcd1234/delta",
            "lane": "needs",
            "status": "needs_review",
            "verdict": "needs_review",
            "reason": "no new diff",
            "cursor_message": r"D:\wt\abcd1234\delta\tests\test_admin.py is on disk",
            "error": "worker made no changes",
            "model": "composer-2.5",
        }
    )
    fixture = {
        "goal": "Hold the merge.",
        "repo": str(repo),
        "stage": "fleet",
        "elapsed_s": 12,
        "tasks": tasks,
        "report": {"integration": {"branch": "tf/integrated", "base": "abc"}},
        "events": [],
        "guards": [],
        "usage": [],
        "proof": {},
    }
    app = create_app(repo, root=ROOT, token="test-token", fixture=fixture, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            cards = "\n".join(page.locator(".card").all_inner_texts())
            assert "git -C" not in cards
            assert r"D:\wt" not in cards
            needs = page.locator("#needs").inner_text()
            assert "needs review" in needs
            assert "No new changes since last review" in needs
            assert page.locator("#working .chip.verdict").count() == 0
            page.locator("#needs .card[data-task-id='held']").click()
            page.locator(".drawer [data-pane='checks']").click()
            detail = page.locator("#checks").text_content()
            assert detail is not None
            assert "git -C" in detail
            assert "D:\\wt" in detail
            assert "D:\\wt" in detail
            label = page.locator("#git-graph .graph-label[data-task-id='t8']")
            style = label.evaluate(
                """el => {
                  const s = getComputedStyle(el);
                  return {
                    overflow: s.overflow,
                    ellipsis: s.textOverflow,
                    min: s.minWidth,
                    flex: s.flexGrow,
                    title: el.title,
                    text: el.textContent,
                  };
                }"""
            )
            assert style["overflow"] == "hidden"
            assert style["ellipsis"] == "ellipsis"
            assert style["min"] == "0px"
            assert style["flex"] == "1"
            assert style["title"] == style["text"]
            assert "feature-branch-name" in style["title"]
            page.locator("#needs").screenshot(path=str(evidence_path("phase-8/ui-v2/3d/needs-1440.png")))
            page.locator("button[data-graph-more]").click()
            order = page.locator("#git-graph").evaluate(
                """el => {
                  const nodes = [...el.querySelectorAll('[data-graph-more]')];
                  const integrated = el.querySelector('[data-graph-integrated]');
                  const more = nodes[0];
                  return {
                    count: nodes.length,
                    text: more ? more.textContent : '',
                    after: !!(
                      integrated && more
                      && (integrated.compareDocumentPosition(more) & Node.DOCUMENT_POSITION_FOLLOWING)
                    ),
                  };
                }"""
            )
            assert order["count"] == 1
            assert order["text"] == "Show less"
            assert order["after"] is True
            page.locator("#git-graph").screenshot(path=str(evidence_path("phase-8/ui-v2/3d/graph16-expanded-1440.png")))
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
