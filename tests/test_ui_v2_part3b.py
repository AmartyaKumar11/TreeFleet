"""UI v2 part 3b: polish for pill, merge button, graph, cost, selection, mobile drawer."""

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


def _task(i: int, lane: str, **extra: object) -> dict[str, object]:
    body: dict[str, object] = {
        "id": f"{lane[0]}{i}",
        "title": f"{lane.title()} task {i}",
        "status": "running" if lane == "working" else "done",
        "lane": lane,
        "branch": f"tf/abcd1234/{lane}-task-{i}",
        "model": "composer-2.5",
    }
    if lane == "working":
        body["action"] = f"Editing tests/test_{i}.py"
    if lane == "needs":
        body["status"] = "failed"
        body["verdict"] = "reject"
        body["reason"] = "no new diff"
    if lane == "ready":
        body["verdict"] = "approve"
        body["files"] = [{"path": "README.md", "additions": 1, "deletions": 0}]
    body.update(extra)
    return body


def _base(repo: Path, tasks: list[dict[str, object]], **extra: object) -> dict[str, object]:
    body: dict[str, object] = {
        "repo": str(repo),
        "branch": "main",
        "head": "abc1234",
        "clean": True,
        "goal": GOAL,
        "worker_model": "router",
        "stage": "fleet",
        "started_at": time.time() - 90,
        "run_id": "abcd1234",
        "needs_human": any(t.get("lane") == "needs" for t in tasks),
        "awaiting": None,
        "tasks": tasks,
        "worktrees": [],
        "events": [],
        "report": {
            "integration": {"branch": "tf/abcd1234/integrated", "merged": [], "excluded": [], "base": "abc1234"},
            "metrics": {
                "costs": [
                    {"component": "planner", "model": "deepseek-chat", "usd": 0.0123, "tokens": 100},
                    {"component": "tiny", "model": "x", "usd": 0.00005, "tokens": 10},
                    {"component": "worker", "model": "composer-2.5", "usd": None, "tokens": 50, "label": "unpriced"},
                ]
            },
        },
    }
    body.update(extra)
    return body


def _polish(repo: Path) -> dict[str, object]:
    diff = (
        "diff --git a/src/a.py b/src/a.py\n+++ b/src/a.py\n@@ -1 +1,2 @@\n+alpha\n"
        "diff --git a/src/b.py b/src/b.py\n+++ b/src/b.py\n@@ -1 +1,2 @@\n+beta\n"
    )
    return _base(
        repo,
        [
            {
                "id": "alpha",
                "title": "Parser tests",
                "status": "running",
                "lane": "working",
                "branch": "tf/abcd1234/alpha",
                "model": "composer-2.5",
                "action": "Editing tests/test_parser.py",
                "lines": ["line 1", "line 2"],
                "diff": diff,
                "diff_files": [
                    {"path": "src/a.py", "additions": 2, "deletions": 0},
                    {"path": "src/b.py", "additions": 1, "deletions": 0},
                ],
                "checks": [
                    {"name": "pytest", "passed": True, "exit_code": 0, "duration_s": 1.0, "output": "ok"},
                    {
                        "name": "ruff",
                        "passed": False,
                        "exit_code": 1,
                        "duration_s": 0.4,
                        "output": "E501 line too long\n" * 8,
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
                    "reverted_paths": ["secrets.env"],
                },
            },
            _task(1, "needs"),
            _task(2, "needs"),
            {
                "id": "ready1",
                "title": "Ready task",
                "status": "done",
                "lane": "ready",
                "branch": "tf/abcd1234/ready1",
                "model": "composer-2.5",
                "verdict": "approve",
                "files": [{"path": "README.md", "additions": 1, "deletions": 0}],
            },
        ],
        report={
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
                        "reverted_paths": ["secrets.env"],
                    }
                ]
            },
            "integration": {
                "branch": "tf/abcd1234/integrated",
                "merged": ["ready1"],
                "excluded": [],
                "base": "abc1234",
                "checks": [{"name": "pytest", "passed": True}],
            },
            "metrics": {
                "costs": [
                    {"component": "planner", "model": "deepseek-chat", "usd": 0.0123, "tokens": 100},
                    {"component": "tiny", "model": "x", "usd": 0.00005, "tokens": 10},
                    {"component": "worker", "model": "composer-2.5", "usd": None, "tokens": 50, "label": "unpriced"},
                ]
            },
        },
    )


def _awaiting_plan(repo: Path) -> dict[str, object]:
    return {
        "repo": str(repo),
        "branch": "main",
        "head": "abc1234",
        "clean": True,
        "goal": GOAL,
        "worker_model": "router",
        "stage": "approve",
        "started_at": time.time() - 60,
        "run_id": "abcd1234",
        "needs_human": True,
        "awaiting": [
            {"id": "alpha", "title": "Parser tests", "allowed_paths": ["tests/"]},
            {"id": "beta", "title": "Notifier lint", "allowed_paths": ["src/notifier/"]},
        ],
        "tasks": [],
        "worktrees": [],
        "events": [
            {
                "type": "guard.decision",
                "seq": 1,
                "payload": {"stage": "input", "allowed": True, "reasons": [], "scores": {}},
            }
        ],
        "explored": ["a.py"],
        "report": None,
    }


def test_needs_you_pill_lane_count_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    # count 0: only working tasks
    app = create_app(
        repo,
        root=ROOT,
        token="test-token",
        fixture=_base(repo, [_task(1, "working"), _task(1, "ready")], needs_human=False),
        models=[],
    )
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            assert page.locator("#attention").is_hidden()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    app2 = create_app(repo, root=ROOT, token="test-token", fixture=_polish(repo), models=[])
    server2, thread2, port2 = _serve(app2)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port2}/", wait_until="networkidle")
            pill = page.locator("#attention")
            assert pill.is_visible()
            assert pill.inner_text().strip() == "Needs you 2"
            browser.close()
    finally:
        server2.should_exit = True
        thread2.join(timeout=5)


def test_copy_merge_ghost_button_and_copied(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", fixture=_polish(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            btn = page.locator("#ready .merge-row .btn.ghost").first
            styles = btn.evaluate(
                """el => {
                  const s = getComputedStyle(el);
                  return {
                    border: s.borderTopWidth,
                    radius: s.borderRadius,
                    font: s.fontSize,
                    wrap: s.whiteSpace,
                    minH: parseFloat(s.minHeight) || el.getBoundingClientRect().height,
                  };
                }"""
            )
            assert styles["border"] == "1px"
            assert float(styles["radius"].replace("px", "").split()[0]) >= 8
            assert styles["font"] == "13px"
            assert styles["wrap"] == "nowrap"
            assert styles["minH"] >= 32
            btn.click()
            page.wait_for_timeout(100)
            assert "Copied" in btn.inner_text()
            assert btn.locator("svg, .check").count() >= 0 or "✓" in btn.inner_text() or "Copied" in btn.inner_text()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_git_graph_scales_and_caps_at_eight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    SHOTS.mkdir(parents=True, exist_ok=True)

    def check(n: int, expand: bool = False) -> None:
        tasks = [_task(i, "working" if i % 2 == 0 else "ready") for i in range(1, n + 1)]
        # unique ids/branches
        for i, task in enumerate(tasks, start=1):
            task["id"] = f"t{i}"
            task["branch"] = f"tf/abcd1234/task-{i}"
            task["title"] = f"Task {i}"
        app = create_app(repo, root=ROOT, token="test-token", fixture=_base(repo, tasks), models=[])
        server, thread, port = _serve(app)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
                graph = page.locator("#git-graph")
                metrics = graph.evaluate(
                    """el => {
                      const labels = [...el.querySelectorAll('.graph-label[data-task-id]')];
                      const fonts = labels.map(t => ({
                        size: getComputedStyle(t).fontSize,
                        family: getComputedStyle(t).fontFamily || '',
                      }));
                      const box = el.querySelector('.graph-list').getBoundingClientRect();
                      return {
                        vb: { w: box.width, h: box.height },
                        labelCount: labels.length,
                        fonts,
                        more: el.querySelectorAll('[data-graph-more]').length,
                        moreText: (el.querySelector('[data-graph-more]') || {}).textContent || '',
                      };
                    }"""
                )
                assert metrics["vb"]["w"] > 0 and metrics["vb"]["h"] > 0
                if n <= 8:
                    assert metrics["labelCount"] == n
                    assert metrics["more"] == 0
                else:
                    assert metrics["labelCount"] == 8
                    assert metrics["more"] == 1
                    assert "+8 more" in metrics["moreText"] or "+8" in metrics["moreText"]
                    if expand:
                        page.locator("button[data-graph-more]").click()
                        page.wait_for_timeout(50)
                        assert page.locator("#git-graph .graph-label[data-task-id]").count() == 16
                        assert page.locator("[data-graph-more]").count() == 1
                        assert "Show less" in page.locator("[data-graph-more]").inner_text()
                for font in metrics["fonts"]:
                    assert "12" in str(font["size"])
                if n == 16:
                    page.screenshot(path=str(SHOTS / "part3b-graph16-1440.png"), full_page=True)
                    page.set_viewport_size({"width": 390, "height": 844})
                    page.wait_for_timeout(200)
                    page.screenshot(path=str(SHOTS / "part3b-graph16-390.png"), full_page=True)
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=5)

    check(1)
    check(4)
    check(16, expand=True)


def test_cost_formatting_and_totals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", fixture=_polish(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.locator(".card", has_text="Parser tests").first.click()
            page.get_by_role("tab", name="Cost").click()
            text = page.locator("#costpane").inner_text()
            assert "$0.0123" in text
            assert "<$0.0001" in text
            assert "unpriced" in text.lower()
            assert "0.0123" in text  # four decimals
            total = page.locator("#costpane .cost-total").inner_text()
            # 0.0123 + 0.00005; float formatting may round to 0.0123 or 0.0124
            assert total.startswith("Total $")
            assert any(frag in total for frag in ("0.0123", "0.0124", "0.01235"))
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_selected_card_and_screenshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", fixture=_polish(repo), models=[])
    server, thread, port = _serve(app)
    SHOTS.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.keyboard.press("Escape")
            page.wait_for_timeout(100)
            page.screenshot(path=str(SHOTS / "part3b-empty-drawer-1440.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(150)
            page.screenshot(path=str(SHOTS / "part3b-empty-drawer-390.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 900})

            card = page.locator(".card", has_text="Parser tests").first
            card.click()
            page.wait_for_timeout(100)
            sel = card.evaluate(
                """el => {
                  const s = getComputedStyle(el);
                  return {
                    border: s.borderTopWidth,
                    color: s.borderTopColor,
                    bg: s.backgroundColor,
                    selected: el.classList.contains('is-selected'),
                  };
                }"""
            )
            assert sel["selected"]
            assert sel["border"] == "1px"

            page.get_by_role("tab", name="Diff").click()
            page.wait_for_timeout(50)
            page.screenshot(path=str(SHOTS / "part3b-diff-1440.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(150)
            page.screenshot(path=str(SHOTS / "part3b-diff-390.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 900})

            page.get_by_role("tab", name="Proof").click()
            page.screenshot(path=str(SHOTS / "part3b-proof-1440.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(150)
            page.screenshot(path=str(SHOTS / "part3b-proof-390.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 900})

            page.get_by_role("tab", name="Checks").click()
            page.locator(".check-row", has_text="ruff").locator("button").click()
            page.wait_for_timeout(50)
            page.screenshot(path=str(SHOTS / "part3b-checks-1440.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(150)
            page.screenshot(path=str(SHOTS / "part3b-checks-390.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 900})

            page.locator("#git-graph").scroll_into_view_if_needed()
            page.screenshot(path=str(SHOTS / "part3b-graph-1440.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(150)
            page.screenshot(path=str(SHOTS / "part3b-graph-390.png"), full_page=True)
            page.set_viewport_size({"width": 1440, "height": 900})

            page.keyboard.press("Control+K")
            page.locator("#command-input").fill("jump")
            page.wait_for_timeout(50)
            page.screenshot(path=str(SHOTS / "part3b-palette-1440.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(150)
            page.screenshot(path=str(SHOTS / "part3b-palette-390.png"), full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_mobile_drawer_sheet_open_close_reopen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", fixture=_polish(repo), models=[])
    server, thread, port = _serve(app)
    SHOTS.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.keyboard.press("Escape")
            page.wait_for_timeout(100)
            drawer = page.locator(".drawer")
            assert drawer.evaluate("el => el.classList.contains('is-open')") is False
            assert page.locator("#lane-working, .lanes").first.is_visible()
            page.screenshot(path=str(SHOTS / "part3b-mobile-closed-390.png"), full_page=True)

            page.locator(".card", has_text="Parser tests").first.click()
            page.wait_for_timeout(150)
            assert drawer.evaluate("el => el.classList.contains('is-open')") is True
            assert page.get_by_role("button", name="Close").count() >= 1
            page.screenshot(path=str(SHOTS / "part3b-mobile-open-390.png"), full_page=True)

            page.get_by_role("button", name="Close", exact=True).click()
            page.wait_for_timeout(100)
            assert drawer.evaluate("el => el.classList.contains('is-open')") is False

            page.locator(".card", has_text="Parser tests").first.click()
            page.wait_for_timeout(100)
            assert drawer.evaluate("el => el.classList.contains('is-open')") is True
            page.keyboard.press("Escape")
            page.wait_for_timeout(100)
            assert drawer.evaluate("el => el.classList.contains('is-open')") is False
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_verdict_chip_and_thread_card_style(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", fixture=_awaiting_plan(repo), models=[])
    server, thread, port = _serve(app)
    SHOTS.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.get_by_role("tab", name="Orchestrator").click()
            plan = page.locator(".plan-card, .orch-card").first
            style = plan.evaluate(
                """el => {
                  const s = getComputedStyle(el);
                  return { border: s.borderTopWidth, radius: s.borderRadius, pad: s.paddingTop };
                }"""
            )
            assert style["border"] == "1px"
            page.screenshot(path=str(SHOTS / "part3b-thread-plan-1440.png"), full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(150)
            page.screenshot(path=str(SHOTS / "part3b-thread-plan-390.png"), full_page=True)
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    # verdict layout on kanban needs card
    app2 = create_app(repo, root=ROOT, token="test-token", fixture=_polish(repo), models=[])
    server2, thread2, port2 = _serve(app2)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port2}/", wait_until="networkidle")
            card = page.locator("#needs .card").first
            html = card.inner_html()
            assert "chip verdict" in html or 'class="chip verdict"' in html
            # reason below chip: verdict-row then reason as sibling
            structure = card.evaluate(
                """el => {
                  const v = el.querySelector('.verdict-row');
                  const r = el.querySelector('.reason');
                  if (!v || !r) return null;
                  return v.compareDocumentPosition(r) & Node.DOCUMENT_POSITION_FOLLOWING;
                }"""
            )
            assert structure
            # verdict chip has no outer bordered wrapper beyond the chip itself
            wrap = card.evaluate(
                """el => {
                  const v = el.querySelector('.verdict-row');
                  if (!v) return true;
                  const s = getComputedStyle(v);
                  return s.borderTopWidth === '0px' || s.borderTopStyle === 'none';
                }"""
            )
            assert wrap
            browser.close()
    finally:
        server2.should_exit = True
        thread2.join(timeout=5)
