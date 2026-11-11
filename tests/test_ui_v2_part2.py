"""UI v2 part 2: orchestrator thread, single-state run strip, sticky top bar."""

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


def _base(repo: Path, **extra: object) -> dict:
    body = {
        "repo": str(repo),
        "branch": "main",
        "head": "abc1234",
        "clean": True,
        "replay": False,
        "goal": GOAL,
        "worker_model": "composer-2.5",
        "started_at": time.time() - 102,
        "run_id": "abcd1234",
        "needs_human": False,
        "awaiting": None,
        "tasks": [],
        "worktrees": [],
        "events": [],
        "report": None,
        "message": "",
        "explored": [
            "src/treefleet/ui/board.html",
            "src/treefleet/ui/lanes.py",
            "tests/test_parser.py",
            "README.md",
            "pyproject.toml",
        ],
    }
    body.update(extra)
    return body


def _guard_ok() -> dict:
    return {
        "type": "guard.decision",
        "seq": 1,
        "payload": {
            "stage": "input",
            "allowed": True,
            "reasons": ["length ok", "no secret pattern"],
            "scores": {"prompt_injection": 0.02, "jailbreak": 0.01},
        },
    }


def _fixture_blocked(repo: Path) -> dict:
    return _base(
        repo,
        stage="guard",
        message="input blocked: goal exceeds the length cap",
        events=[
            {
                "type": "guard.decision",
                "seq": 1,
                "payload": {
                    "stage": "input",
                    "allowed": False,
                    "reasons": ["goal exceeds the length cap"],
                    "scores": {"prompt_injection": 0.91},
                },
            }
        ],
        explored=[],
    )


def _fixture_awaiting(repo: Path) -> dict:
    return _base(
        repo,
        stage="approve",
        needs_human=True,
        awaiting=[
            {"id": "alpha", "title": "Parser tests", "allowed_paths": ["tests/"]},
            {"id": "beta", "title": "Notifier lint", "allowed_paths": ["src/notifier/"]},
            {"id": "gamma", "title": "Config docs", "allowed_paths": ["README.md"]},
        ],
        events=[_guard_ok(), {"type": "phase", "seq": 2, "payload": {"name": "scan", "file_count": 5}}],
        tasks=[
            {
                "id": "alpha",
                "title": "Parser tests",
                "lane": "working",
                "status": "",
                "branch": "tf/abcd1234/alpha",
                "model": "composer-2.5",
            }
        ],
    )


def _fixture_running(repo: Path) -> dict:
    return _base(
        repo,
        stage="fleet",
        events=[_guard_ok(), {"type": "phase", "seq": 2, "payload": {"name": "fleet"}}],
        tasks=[
            {
                "id": "alpha",
                "title": "Parser tests",
                "lane": "working",
                "status": "running",
                "branch": "tf/abcd1234/alpha",
                "model": "composer-2.5",
                "action": "Editing tests/test_parser.py",
                "lines": ["Editing tests/test_parser.py"],
            },
            {
                "id": "beta",
                "title": "Notifier lint",
                "lane": "working",
                "status": "running",
                "branch": "tf/abcd1234/beta",
                "model": "composer-2.5-fast",
                "action": "Reading src/notifier/mod.py",
            },
            {
                "id": "gamma",
                "title": "Config docs",
                "lane": "review",
                "status": "done",
                "verdict": "approve",
                "branch": "tf/abcd1234/gamma",
                "model": "composer-2.5",
            },
        ],
    )


def _fixture_needs_review(repo: Path) -> dict:
    return _base(
        repo,
        stage="review",
        needs_human=True,
        events=[
            _guard_ok(),
            {"type": "phase", "seq": 2, "payload": {"name": "review"}},
            {
                "type": "review_verdict",
                "seq": 3,
                "task_id": "beta",
                "payload": {"verdict": "reject", "reason": "no new diff", "status": "needs_review"},
            },
        ],
        tasks=[
            {
                "id": "alpha",
                "title": "Parser tests",
                "lane": "review",
                "status": "done",
                "verdict": "approve",
                "branch": "tf/abcd1234/alpha",
                "model": "composer-2.5",
            },
            {
                "id": "beta",
                "title": "Notifier lint",
                "lane": "needs",
                "status": "needs_review",
                "verdict": "reject",
                "reason": "no new diff",
                "branch": "tf/abcd1234/beta",
                "model": "composer-2.5-fast",
            },
            {
                "id": "gamma",
                "title": "Config docs",
                "lane": "review",
                "status": "done",
                "verdict": "approve",
                "branch": "tf/abcd1234/gamma",
                "model": "composer-2.5",
            },
        ],
    )


def _fixture_finished(repo: Path) -> dict:
    return _base(
        repo,
        stage="done",
        finished_at=time.time(),
        events=[_guard_ok(), {"type": "phase", "seq": 2, "payload": {"name": "integrate"}}],
        tasks=[
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
                "id": "beta",
                "title": "Notifier lint",
                "lane": "ready",
                "status": "done",
                "verdict": "approve",
                "branch": "tf/abcd1234/beta",
                "model": "composer-2.5",
            },
            {
                "id": "gamma",
                "title": "Config docs",
                "lane": "needs",
                "status": "failed",
                "verdict": "reject",
                "reason": "merge conflict",
                "branch": "tf/abcd1234/gamma",
                "model": "composer-2.5",
            },
        ],
        report={
            "schema_id": "treefleet.final_report.v1",
            "goal": GOAL,
            "integration": {
                "branch": "tf/abcd1234/integrated",
                "merged": ["alpha", "beta"],
                "excluded": ["gamma: merge conflict"],
                "checks": [{"name": "pytest", "passed": True}],
            },
            "verdicts": [
                {"task_id": "alpha", "verdict": "approve", "reasons": []},
                {"task_id": "beta", "verdict": "approve", "reasons": []},
                {"task_id": "gamma", "verdict": "reject", "reasons": ["merge conflict"]},
            ],
            "metrics": {
                "wall_seconds": 126.2,
                "sequential_seconds": 236.0,
                "speedup": 1.87,
                "fleet_speedup": 2.4,
                "total_usd": 0.042,
                "costs": [
                    {"component": "planner", "model": "deepseek/deepseek-flash", "usd": 0.005, "label": ""},
                    {"component": "cursor", "model": "composer-2.5", "usd": 0.037, "label": "estimate"},
                ],
            },
        },
    )


def test_run_strip_is_single_state_and_topbar_does_not_overlap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    fixture = _fixture_running(repo)
    # pad the page so we can scroll
    fixture["tasks"] = fixture["tasks"] * 6
    for index, task in enumerate(fixture["tasks"]):
        task = dict(task)
        task["id"] = f"{task['id']}-{index}"
        task["title"] = f"{task['title']} case {index}"
        fixture["tasks"][index] = task
    app = create_app(repo, root=ROOT, token="test-token", fixture=fixture, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            status = page.locator("#run-status").inner_text()
            assert "Running" in status
            assert "Awaiting" not in status
            assert "Needs plan approval" not in status
            page.evaluate("document.querySelector('.board-body').scrollTop = 600")
            page.wait_for_timeout(150)
            overlap = page.evaluate(
                """() => {
                  const bar = document.querySelector('.topbar');
                  const body = document.querySelector('.board-body');
                  const br = bar.getBoundingClientRect();
                  const bodyTop = body.getBoundingClientRect().top;
                  return bodyTop >= br.bottom - 1;
                }"""
            )
            assert overlap is True
            page.screenshot(path=str(evidence_path("phase-8/ui-v2/part2-scroll-topbar-1440.png")))
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.parametrize(
    ("name", "factory", "expect"),
    [
        (
            "blocked",
            _fixture_blocked,
            {
                "strip": "Blocked",
                "texts": ["Input guard blocked", "goal exceeds the length cap"],
                "no": ["Started", "Approve plan"],
            },
        ),
        (
            "awaiting",
            _fixture_awaiting,
            {
                "strip": "Awaiting approval",
                "texts": [
                    "Input guard passed",
                    "Explored 5 files",
                    "Started 3 workers, each in its own worktree",
                    "Approve plan",
                    "Cancel",
                ],
                "no": [],
            },
        ),
        (
            "running",
            _fixture_running,
            {
                "strip": "Running",
                "texts": ["Started 3 workers", "Parser tests", "running"],
                "no": [],
            },
        ),
        (
            "needs-review",
            _fixture_needs_review,
            {
                "strip": "Reviewing",
                "texts": ["Reviewing 3 branches", "no new diff", "Notifier lint"],
                "no": [],
            },
        ),
        (
            "finished",
            _fixture_finished,
            {
                "strip": "Done",
                "texts": [
                    "Merged 2, excluded 1",
                    "tf/abcd1234/integrated",
                    "Copy merge command",
                    "1.87",
                ],
                "no": [],
            },
        ),
    ],
)
def test_orchestrator_thread_states_and_screenshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    factory,
    expect: dict,
) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    fixture = factory(repo)
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
            page.get_by_role("tab", name="Orchestrator").click()
            thread_el = page.get_by_role("log", name="Orchestrator thread")
            thread_el.wait_for()
            assert GOAL.split(",")[0] in thread_el.inner_text() or GOAL in thread_el.inner_text()
            strip = page.locator("#run-status").inner_text()
            assert expect["strip"] in strip
            # exactly one primary state word
            states = ["Blocked", "Awaiting approval", "Running", "Reviewing", "Integrating", "Done"]
            assert sum(1 for word in states if word in strip) == 1
            body = thread_el.inner_text()
            for text in expect["texts"]:
                assert text in body
            for text in expect["no"]:
                assert text not in body
            if name == "awaiting":
                page.get_by_role("button", name="Approve plan").first.click()
                page.wait_for_timeout(100)
                assert "Running" in page.locator("#run-status").inner_text()
                assert "starting" in thread_el.inner_text().lower()
                assert page.get_by_role("button", name="Approve plan").count() == 0
            if name == "running":
                page.locator(".worker-row", has_text="Parser tests").first.click()
                assert page.locator("#task-title").inner_text() == "Parser tests"
            page.get_by_label("Follow-up goal").wait_for()
            page.screenshot(path=str(evidence_path(f"phase-8/ui-v2/part2-{name}-1440.png")), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(200)
            page.screenshot(path=str(evidence_path(f"phase-8/ui-v2/part2-{name}-390.png")), full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_orchestrator_jump_to_latest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    fixture = _fixture_running(repo)
    fixture["tasks"] = [
        {
            **fixture["tasks"][0],
            "id": f"t{i}",
            "title": f"Long running worker task number {i} for scroll",
            "branch": f"tf/abcd1234/task-{i}-long-name",
            "lines": [f"line {n}" for n in range(20)],
        }
        for i in range(12)
    ]
    app = create_app(repo, root=ROOT, token="test-token", fixture=fixture, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.get_by_role("tab", name="Orchestrator").click()
            thread_el = page.locator("#orch-thread")
            thread_el.wait_for()
            page.evaluate(
                """() => {
                  const el = document.querySelector('#orch-thread');
                  el.scrollTop = 0;
                  el.dispatchEvent(new Event('scroll'));
                }"""
            )
            page.wait_for_timeout(100)
            jump = page.get_by_role("button", name="Jump to latest")
            jump.wait_for()
            jump.click()
            assert jump.is_hidden()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
