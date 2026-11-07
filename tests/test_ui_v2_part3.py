"""UI v2 part 3: drawer tabs, git graph, command palette, XSS safety."""

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
XSS = '<img src=x onerror="window.__xss=1"><script>window.__xss=1</script>'
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


def _rich_fixture(repo: Path) -> dict[str, object]:
    branch_xss = f"tf/abcd1234/{XSS}"
    lines = [f"line {n}" for n in range(120)]
    lines[10] = f"early {XSS}"
    lines[-1] = XSS
    diff_text = (
        f"diff --git a/{XSS} b/{XSS}\n"
        "--- a/src/parser.py\n"
        "+++ b/src/parser.py\n"
        "@@ -1,3 +1,4 @@\n"
        " def parse():\n"
        f"+{XSS}\n"
        "     return 1\n"
    )
    return {
        "repo": str(repo),
        "branch": "main",
        "head": "abc1234",
        "clean": True,
        "goal": GOAL,
        "worker_model": "router",
        "stage": "fleet",
        "started_at": time.time() - 90,
        "run_id": "abcd1234",
        "needs_human": False,
        "awaiting": None,
        "tasks": [
            {
                "id": "alpha",
                "title": "Parser tests",
                "status": "running",
                "lane": "working",
                "branch": branch_xss,
                "model": "composer-2.5",
                "action": "Editing tests/test_parser.py",
                "lines": lines,
                "diff": diff_text,
                "diff_files": [
                    {"path": XSS, "additions": 2, "deletions": 0},
                    {"path": "src/parser.py", "additions": 1, "deletions": 0},
                    {"path": "assets/icon.bin", "additions": 0, "deletions": 0, "binary": True},
                ],
                "checks": [
                    {
                        "name": "pytest",
                        "passed": True,
                        "exit_code": 0,
                        "duration_s": 1.2,
                        "output": "ok",
                    },
                    {
                        "name": "ruff",
                        "passed": False,
                        "exit_code": 1,
                        "duration_s": 0.4,
                        "output": XSS,
                    },
                ],
                "proof": {
                    "task_id": "alpha",
                    "process_cwd_matches": True,
                    "toplevel_matches": True,
                    "registered_in_worktree_list": True,
                    "canary_absent": True,
                    "changes_within_allowed_paths": False,
                    "contained_violation": True,
                    "reverted_paths": ["secrets.env", XSS],
                },
                "commits": [{"sha": "aaa1111", "summary": "add parser tests"}],
            },
            {
                "id": "beta",
                "title": "Notifier lint",
                "status": "done",
                "lane": "ready",
                "branch": "tf/abcd1234/beta",
                "model": "composer-2.5",
                "verdict": "approve",
                "files": [{"path": "src/notifier.py", "additions": 3, "deletions": 1}],
                "diff": "diff --git a/src/notifier.py b/src/notifier.py\n+print(1)\n",
                "diff_files": [{"path": "src/notifier.py", "additions": 3, "deletions": 1}],
                "checks": [{"name": "pytest", "passed": True, "exit_code": 0, "duration_s": 0.8, "output": "pass"}],
                "commits": [{"sha": "bbb2222", "summary": "fix notifier"}],
            },
        ],
        "worktrees": [
            {
                "path": str(repo / "wt-alpha"),
                "branch": branch_xss,
                "changed": 2,
                "files": [{"path": "src/parser.py", "additions": 1, "deletions": 0}],
                "head": "aaa1111",
            },
            {
                "path": str(repo / "wt-beta"),
                "branch": "tf/abcd1234/beta",
                "changed": 1,
                "files": [{"path": "src/notifier.py", "additions": 3, "deletions": 1}],
                "head": "bbb2222",
            },
        ],
        "events": [],
        "report": {
            "isolation": {
                "tasks": [
                    {
                        "task_id": "alpha",
                        "process_cwd_matches": True,
                        "toplevel_matches": True,
                        "registered_in_worktree_list": True,
                        "canary_absent": True,
                        "changes_within_allowed_paths": False,
                        "contained_violation": True,
                        "reverted_paths": ["secrets.env", XSS],
                    }
                ]
            },
            "integration": {
                "branch": "tf/abcd1234/integrated",
                "merged": ["beta"],
                "excluded": [],
                "checks": [{"name": "pytest", "passed": True}],
                "base": "abc1234",
            },
            "metrics": {
                "wall_seconds": 90,
                "total_usd": 0.02,
                "costs": [
                    {"component": "planner", "model": "deepseek-chat", "usd": 0.01, "tokens": 1200},
                    {"component": "worker", "model": "composer-2.5", "usd": None, "tokens": 500, "label": "unpriced"},
                    {
                        "component": "reviewer",
                        "model": "deepseek-chat",
                        "usd": None,
                        "tokens": None,
                        "label": "usage not reported",
                    },
                ],
            },
        },
    }


def test_xss_payloads_render_literally(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", fixture=_rich_fixture(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.locator(".card", has_text="Parser tests").first.click()
            page.wait_for_timeout(150)
            assert page.evaluate("() => window.__xss") in (None, False)
            live = page.locator("#live")
            assert XSS in live.inner_text()
            assert live.locator("img").count() == 0
            assert live.locator("script").count() == 0
            page.get_by_role("tab", name="Diff").click()
            diff = page.locator("#diff")
            assert XSS in diff.inner_text()
            assert diff.locator("img").count() == 0
            assert diff.locator("script").count() == 0
            meta = page.locator("#task-meta").inner_text()
            assert XSS in meta
            page.get_by_role("tab", name="Checks").click()
            checks = page.locator("#checks")
            page.locator(".check-row", has_text="ruff").locator("button").click()
            assert XSS in checks.inner_text()
            assert checks.locator("img").count() == 0
            assert checks.locator("script").count() == 0
            assert page.evaluate("() => window.__xss") in (None, False)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_drawer_tabs_live_diff_proof_checks_cost(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", fixture=_rich_fixture(repo), models=[])
    server, thread, port = _serve(app)
    SHOTS.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.locator(".card", has_text="Parser tests").first.click()
            tabs = page.locator(".drawer [role='tablist'] [role='tab']")
            assert tabs.count() == 5
            for name in ("Live output", "Diff", "Proof", "Checks", "Cost"):
                assert page.locator(".drawer").get_by_role("tab", name=name).count() == 1

            live = page.locator("#live")
            assert live.is_visible()
            assert page.get_by_role("button", name="Pause").count() == 1
            assert page.get_by_role("button", name="Copy", exact=True).count() >= 1
            assert (
                "Show earlier lines" in live.inner_text()
                or page.get_by_role("button", name="Show earlier lines").count()
            )

            page.get_by_role("tab", name="Diff").click()
            diff = page.locator("#diff")
            assert diff.locator(".diff-files").count() == 1
            assert "+2" in diff.inner_text() or "+1" in diff.inner_text()
            assert diff.locator(".diff-line.add, .diff-add").count() >= 1
            assert "binary" in diff.inner_text().lower()
            assert diff.locator(".diff-line .ln, .diff-ln").count() >= 1

            page.get_by_role("tab", name="Proof").click()
            proof = page.locator("#proofpane")
            assert "Process cwd" in proof.inner_text()
            assert "reverted" in proof.inner_text().lower() or "secrets.env" in proof.inner_text()

            page.get_by_role("tab", name="Checks").click()
            checks = page.locator("#checks")
            assert "pytest" in checks.inner_text()
            assert "pass" in checks.inner_text().lower() or "fail" in checks.inner_text().lower()
            assert "exit" in checks.inner_text().lower() or "1.2" in checks.inner_text()

            page.get_by_role("tab", name="Cost").click()
            cost = page.locator("#costpane")
            assert "planner" in cost.inner_text().lower()
            assert "unpriced" in cost.inner_text().lower()
            assert "usage not reported" in cost.inner_text().lower()

            page.screenshot(path=str(SHOTS / "part3-drawer-1440.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(200)
            assert page.locator(".drawer").evaluate(
                """el => {
                  const s = getComputedStyle(el);
                  return s.position === 'fixed' || el.classList.contains('sheet')
                    || el.getBoundingClientRect().height > 400;
                }"""
            )
            page.screenshot(path=str(SHOTS / "part3-drawer-390.png"), full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_git_graph_and_command_palette(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", fixture=_rich_fixture(repo), models=[])
    server, thread, port = _serve(app)
    SHOTS.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            graph = page.locator("#git-graph")
            assert graph.is_visible()
            assert graph.locator("svg").count() == 1
            assert "tf/abcd1234" in graph.inner_text() or graph.locator("[data-task-id]").count() >= 1
            graph.locator("text[data-task-id='alpha']").click(force=True)
            assert page.locator("#task-title").inner_text() == "Parser tests"

            page.keyboard.press("Control+K")
            palette = page.locator("#command-palette")
            assert palette.is_visible()
            assert page.get_by_text("New run").count() >= 1
            assert page.get_by_text("Jump to task").count() >= 1
            assert page.get_by_text("Switch view").count() >= 1
            assert page.get_by_text("Toggle auto-run").count() >= 1
            assert page.get_by_text("Copy merge command").count() >= 1
            page.locator("#command-palette input").fill("jump")
            page.wait_for_timeout(50)
            visible = page.locator("#command-palette [role='option']:visible")
            assert visible.count() >= 1
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Enter")
            page.wait_for_timeout(100)
            page.keyboard.press("Control+K")
            assert page.locator("#command-palette").is_visible()
            page.keyboard.press("Escape")
            assert page.locator("#command-palette").is_hidden()
            page.locator(".card", has_text="Parser tests").first.click()
            page.wait_for_timeout(50)
            page.keyboard.press("Escape")
            assert "No task selected" in page.locator("#task-title").inner_text()
            page.screenshot(path=str(SHOTS / "part3-graph-palette-1440.png"), full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_drawer_a11y_tab_arrows_and_aria_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", fixture=_rich_fixture(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.locator(".card", has_text="Parser tests").first.click()
            first = page.locator(".drawer [role='tab']").first
            first.focus()
            page.keyboard.press("ArrowRight")
            selected = page.locator(".drawer [role='tab'][aria-selected='true']")
            assert selected.inner_text().strip().startswith("Diff")
            assert page.locator("#run-strip").get_attribute("aria-live") == "polite"
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
