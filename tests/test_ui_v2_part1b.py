"""UI v2 part 1b: card polish, composer, run strip, collapsed run details."""

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
SHOTS = ROOT / "evidence" / "phase-8" / "ui-v2"


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


def _task(
    task_id: str,
    *,
    title: str,
    lane: str,
    branch: str,
    model: str = "composer-2.5",
    status: str = "running",
    **extra: object,
) -> dict:
    body: dict = {
        "id": task_id,
        "title": title,
        "status": status,
        "lane": lane,
        "branch": branch,
        "model": model,
    }
    body.update(extra)
    return body


def _polish_fixture(repo: Path) -> dict:
    long_title = (
        "Expand the admin notifier coverage for edge cases across parser, config loaders, and the retry backoff path"
    )
    long_branch = "tf/abcd1234/very-long-worker-name-for-truncation-demo-branch"
    return {
        "repo": str(repo),
        "branch": "main",
        "head": "abc1234",
        "clean": True,
        "replay": False,
        "stage": "fleet",
        "started_at": time.time() - 102,
        "run_id": "abcd1234",
        "needs_human": True,
        "awaiting": [
            {"id": "plan-a", "title": "Parser edge cases", "allowed_paths": ["tests/"]},
            {"id": "plan-b", "title": "Notifier lint cleanup", "allowed_paths": ["src/notifier/"]},
        ],
        "tasks": [
            _task(
                "w1",
                title=long_title,
                lane="working",
                branch=long_branch,
                action="Editing tests/test_admin.py",
                files=[{"path": "tests/test_admin.py", "additions": 12, "deletions": 2}],
                lines=["Editing tests/test_admin.py"],
                age_s=42,
            ),
            _task(
                "w2",
                title="Wire fleet progress events into the live action line",
                lane="working",
                branch="tf/abcd1234/w2-progress-events",
                model="composer-2.5-fast",
                action="Reading src/treefleet/ui/board.html",
                files=[{"path": "src/treefleet/ui/board.html", "additions": 4, "deletions": 1}],
                age_s=18,
            ),
            _task(
                "w3",
                title="Tighten path guards for nested worktree writes",
                lane="working",
                branch="tf/abcd1234/w3-path-guards-nested",
                action="Editing src/treefleet/guards/paths.py",
                age_s=9,
            ),
            _task(
                "w4",
                title="Document the run strip stage stepper for reviewers",
                lane="working",
                branch="tf/abcd1234/w4-docs-run-strip",
                model="cursor-grok-4.6-high",
                action="Editing docs/design-direction.md",
                age_s=3,
            ),
            _task(
                "n1",
                title="Admin module revision after empty worker exit",
                lane="needs",
                branch="tf/abcd1234/n1-admin-empty-exit",
                status="failed",
                verdict="reject",
                reason="no new diff",
                error="worker made no changes",
                round=1,
            ),
            _task(
                "n2",
                title="Held for human merge decision on notifier patch",
                lane="needs",
                branch="tf/abcd1234/n2-held-notifier-merge",
                status="needs_review",
                verdict="reject",
                reason="needs_review: human must confirm merge",
                files=[{"path": "src/notifier/mod.py", "additions": 8, "deletions": 3}],
            ),
            _task(
                "n3",
                title="Config docs blocked by guard on tool result shape",
                lane="needs",
                branch="tf/abcd1234/n3-config-docs-guard",
                status="failed",
                verdict="reject",
                reason="malformed tool result",
                model="composer-2.5-fast",
            ),
            _task(
                "n4",
                title="Retry exhausted for DeepSeek flash on planner step",
                lane="needs",
                branch="tf/abcd1234/n4-llm-retry-exhausted",
                status="failed",
                reason="provider unavailable after retries",
            ),
            _task(
                "r1",
                title="Docs: explain every config option in the README",
                lane="review",
                branch="tf/abcd1234/r1-readme-config-docs",
                status="done",
                verdict="approve",
                model="composer-2.5-fast",
                checks=[{"name": "pytest", "passed": True}],
                files=[{"path": "README.md", "additions": 40, "deletions": 2}],
            ),
            _task(
                "r2",
                title="Parser tests waiting on review verdict",
                lane="review",
                branch="tf/abcd1234/r2-parser-tests-verdict",
                status="done",
                verdict="approve",
                checks=[{"name": "pytest", "passed": True}, {"name": "ruff", "passed": True}],
                files=[{"path": "tests/test_parser.py", "additions": 22, "deletions": 0}],
            ),
            _task(
                "r3",
                title="Isolation proof table rows for the beta worktree",
                lane="review",
                branch="tf/abcd1234/r3-isolation-proof-rows",
                status="done",
                verdict="approve",
            ),
            _task(
                "r4",
                title="Cost metrics panel labels for unpriced Cursor usage",
                lane="review",
                branch="tf/abcd1234/r4-cost-metrics-labels",
                status="done",
                verdict="approve",
                model="composer-2.5-fast",
            ),
            _task(
                "y1",
                title="Ready: notifier lint cleanup on dedicated branch",
                lane="ready",
                branch="tf/abcd1234/y1-notifier-lint-ready",
                status="done",
                verdict="approve",
                checks=[{"name": "pytest", "passed": True}],
                files=[{"path": "src/notifier/mod.py", "additions": 3, "deletions": 1}],
            ),
            _task(
                "y2",
                title="Ready: parser edge cases ready to merge into main",
                lane="ready",
                branch="tf/abcd1234/y2-parser-edge-ready-to-merge",
                status="done",
                verdict="approve",
                files=[{"path": "tests/test_parser.py", "additions": 15, "deletions": 0}],
            ),
            _task(
                "y3",
                title="Ready: path guard hardening with green checks",
                lane="ready",
                branch="tf/abcd1234/y3-path-guard-hardening",
                status="done",
                verdict="approve",
                checks=[{"name": "pytest", "passed": True}],
                files=[{"path": "src/treefleet/guards/paths.py", "additions": 9, "deletions": 2}],
            ),
            _task(
                "y4",
                title="Ready: run details panel empty-state copy",
                lane="ready",
                branch="tf/abcd1234/y4-run-details-empty-copy",
                status="done",
                verdict="approve",
                model="composer-2.5-fast",
                files=[{"path": "src/treefleet/ui/board.html", "additions": 2, "deletions": 0}],
            ),
        ],
        "worktrees": [
            {
                "path": r"C:\wt\abcd1234\w1",
                "branch": long_branch,
                "changed": 1,
                "files": [{"path": "tests/test_admin.py", "additions": 12, "deletions": 2}],
            }
        ],
        "events": [
            {
                "type": "guard.decision",
                "seq": 1,
                "payload": {"stage": "input", "allowed": True, "scores": {"prompt_injection": 0.02}},
            }
        ],
        "proof": [
            {
                "task_id": "w1",
                "process_cwd_matches": True,
                "toplevel_matches": True,
                "registered_in_worktree_list": True,
                "canary_absent": True,
                "changes_within_allowed_paths": True,
            }
        ],
        "report": {
            "metrics": {
                "costs": [
                    {"component": "planner", "model": "deepseek/deepseek-flash", "usd": 0.005439, "label": ""},
                ]
            }
        },
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


def test_polish_layout_and_screenshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    fixture = _polish_fixture(repo)
    app = create_app(
        repo,
        root=ROOT,
        token="test-token",
        fixture=fixture,
        models=[{"id": "composer-2.5", "label": "Composer 2.5"}],
    )
    server, thread, port = _serve(app)
    SHOTS.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")

            composer = page.get_by_label("New run")
            composer.wait_for()
            assert composer.locator("#goal").count() == 1
            assert composer.locator("#model").count() == 1
            assert composer.locator("#run").count() == 1
            assert page.locator("#auto").count() == 1
            assert not page.locator("#auto").is_visible()
            page.get_by_role("button", name="Run settings").click()
            page.locator("#auto").wait_for(state="visible")

            strip = page.locator("#run-strip")
            strip.wait_for()
            assert "Awaiting approval" in strip.inner_text()
            assert "Fleet running" not in strip.inner_text()
            for stage in ("guard", "plan", "approve", "fleet", "review", "integrate"):
                assert stage in strip.inner_text().lower()

            working = page.locator("#lane-working .card[data-task-id]")
            assert working.count() >= 4
            first = working.first
            assert first.locator(".model-row").count() == 1
            assert first.locator(".title").count() == 1
            assert first.locator(".branch").count() == 1
            assert first.locator(".action-line").count() == 1
            assert first.locator(".status").count() == 0
            assert "composer-2.5" in first.inner_text()
            # model appears once (row 1), not again as a footer
            assert first.inner_text().lower().count("composer-2.5") == 1

            needs = page.locator("#lane-needs")
            plan = needs.locator(".card.plan")
            plan.wait_for()
            assert "Plan ready" in plan.inner_text()
            plan.get_by_role("button", name="Approve plan").wait_for()
            plan.get_by_role("button", name="Cancel").wait_for()
            assert plan.locator(".btn.go").count() == 0

            ready = page.locator("#lane-ready")
            ready.get_by_role("button", name="Copy merge command", exact=True).first.wait_for()
            assert page.locator("#lane-working").get_by_role("button", name="Copy merge command").count() == 0

            details = page.get_by_role("region", name="Run details")
            details.wait_for()
            assert page.get_by_label("Branch map").is_hidden()
            details.locator("summary").click()
            details.get_by_role("tab", name="Worktrees").click()
            assert r"C:\wt\abcd1234\w1" in page.get_by_label("Worktrees").inner_text()
            details.get_by_role("tab", name="Guards").click()
            assert "allowed" in page.locator("#guards").inner_text()
            details.get_by_role("tab", name="Proof").click()
            assert "Within scope" in page.get_by_label("Isolation proof").inner_text()

            page.screenshot(path=str(SHOTS / "part1b-desktop-1440.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(200)
            page.screenshot(path=str(SHOTS / "part1b-mobile-390.png"), full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
