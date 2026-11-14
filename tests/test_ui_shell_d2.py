"""15.D2 shell: sidebar, topbar, composer, run strip, responsive, D1 carry-overs."""

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
XSS = '<img src=x onerror="window.__xss=1"><script>window.__xss=1</script>'


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


def _runs_fixture(repo: Path, *, stage: str = "fleet", awaiting: object = None) -> dict:
    now = time.time()
    long_goal = "Build a calm Claude-style board shell with sidebar run history and generous spacing " * 3
    return {
        "repo": str(repo),
        "branch": "main",
        "head": "abc1234",
        "clean": True,
        "goal": long_goal.strip(),
        "worker_model": "router",
        "stage": stage,
        "started_at": now - 120,
        "run_id": "run001",
        "needs_human": stage == "approve",
        "awaiting": awaiting,
        "message": "fleet failed" if stage == "timed_out" else "",
        "tasks": [
            {
                "id": "t1",
                "title": "Shell work",
                "status": "running",
                "lane": "working",
                "branch": "tf/run001/shell",
                "model": "composer-2.5",
                "action": "Editing board.css",
            }
        ],
        "events": [],
        "worktrees": [],
        "runs": [
            {"id": "run001", "started": now - 120, "goal": long_goal[:140], "status": "running"},
            {"id": "run002", "started": now - 400, "goal": "Done run goal text", "status": "done"},
            {"id": "run003", "started": now - 800, "goal": "Needs you goal", "status": "needs"},
            {"id": "run004", "started": now - 2000, "goal": "Failed run goal", "status": "error"},
            {"id": "run005", "started": now - 5000, "goal": "Older success", "status": "done"},
            {"id": "run006", "started": now - 90000, "goal": XSS, "status": "done"},
        ],
    }


@pytest.mark.ui
def test_shell_sidebar_composer_strip_and_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    fixture = _runs_fixture(repo)
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

            rows = page.locator("#run-history button")
            assert rows.count() == 6
            first = rows.first
            assert "run001" in first.inner_text()
            goal = first.locator(".run-goal")
            assert goal.count() == 1
            box = goal.bounding_box()
            assert box and box["height"] < 28
            assert (
                "min ago" in first.locator(".run-time").inner_text()
                or "just now" in first.locator(".run-time").inner_text()
            )
            assert "running" in (first.get_attribute("aria-label") or "")

            rows.nth(1).focus()
            page.keyboard.press("ArrowDown")
            assert page.evaluate("() => document.activeElement === document.querySelectorAll('#run-history button')[2]")

            xss_row = rows.nth(5)
            assert XSS in xss_row.inner_text()
            assert xss_row.locator("img").count() == 0
            assert xss_row.locator("script").count() == 0
            assert page.evaluate("() => window.__xss") in (None, False)

            model_icon = page.locator("#lane-working .card .model use").first.get_attribute("href") or ""
            assert "icon-sparkles" in model_icon
            assert "icon-copy" not in model_icon

            styles = page.locator("#command-input").evaluate(
                """el => {
                  const s = getComputedStyle(el);
                  return { h: s.height, r: s.borderRadius, b: s.borderColor };
                }"""
            )
            assert styles["h"] == "36px"
            assert styles["r"] == "6px"
            assert styles["b"] not in {"", "rgba(0, 0, 0, 0)", "transparent"}

            goal_box = page.locator("#goal")
            goal_box.fill("one\ntwo\nthree")
            page.wait_for_timeout(50)
            before = goal_box.evaluate("el => el.clientHeight")
            goal_box.fill("\n".join(f"line {i}" for i in range(12)))
            page.wait_for_timeout(50)
            after = goal_box.evaluate(
                """el => ({
                  h: el.clientHeight,
                  oy: getComputedStyle(el).overflowY,
                  rows: el.value.split('\\n').length,
                })"""
            )
            assert after["h"] >= before
            assert after["rows"] >= 8
            assert after["oy"] in {"auto", "scroll"}

            submitted = {"n": 0}

            def on_req(route, request) -> None:  # type: ignore[no-untyped-def]
                if request.method == "POST" and request.url.endswith("/api/runs"):
                    submitted["n"] += 1
                    route.fulfill(status=200, content_type="application/json", body='{"run_id":"newrun"}')
                else:
                    route.continue_()

            page.route("**/api/runs", on_req)
            goal_box.fill("submit me")
            page.wait_for_timeout(30)
            assert page.locator("#run").is_enabled()
            goal_box.press("Enter")
            page.wait_for_timeout(80)
            assert submitted["n"] == 0
            goal_box.press("Control+Enter")
            page.wait_for_timeout(120)
            assert submitted["n"] == 1

            page.locator(".card", has_text="Shell work").first.click()
            tabs = page.locator(".drawer .tabs .tab")
            assert tabs.count() >= 2
            t0 = tabs.nth(0).bounding_box()
            t1 = tabs.nth(1).bounding_box()
            assert t0 and t1
            gap = t1["x"] - (t0["x"] + t0["width"])
            assert 14 <= gap <= 20
            assert abs(t0["height"] - 36) <= 2
            underline = tabs.nth(0).evaluate(
                """el => {
                  const s = getComputedStyle(el);
                  return { color: s.color, border: s.borderBottomWidth, bc: s.borderBottomColor };
                }"""
            )
            assert underline["border"] == "2px"

            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.ui
def test_composer_plan_gate_and_failed_submit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    fixture = _runs_fixture(
        repo,
        stage="approve",
        awaiting=[{"id": "p1", "title": "Plan A", "allowed_paths": ["src/"]}],
    )
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
            gate = page.locator("#composer-gate")
            assert gate.is_visible()
            assert "Approve or reject the plan" in gate.inner_text()
            assert page.locator("#run").is_disabled()
            assert page.locator("#new-run").evaluate("el => el.classList.contains('is-disabled')")
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)

    fixture2 = _runs_fixture(repo)
    app2 = create_app(
        repo,
        root=ROOT,
        token="test-token",
        fixture=fixture2,
        models=[{"id": "composer-2.5", "label": "Composer 2.5"}],
    )
    server2, thread2, port2 = _serve(app2)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port2}/", wait_until="networkidle")
            page.route(
                "**/api/runs",
                lambda route, request: (
                    route.fulfill(status=400, content_type="application/json", body='{"error":"input blocked"}')
                    if request.method == "POST"
                    else route.continue_()
                ),
            )
            page.locator("#goal").fill("keep this text on failure")
            page.locator("#run").click()
            page.wait_for_timeout(150)
            assert page.locator("#goal").input_value() == "keep this text on failure"
            assert page.locator("#error").is_visible()
            assert "input blocked" in page.locator("#error").inner_text()
            browser.close()
    finally:
        server2.should_exit = True
        thread2.join(timeout=5)


@pytest.mark.ui
def test_run_strip_states_and_responsive_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)

    def check(stage: str, expect_failed: bool = False) -> None:
        fixture = _runs_fixture(repo, stage=stage)
        if stage == "done":
            fixture["finished_at"] = time.time()
        app = create_app(repo, root=ROOT, token="test-token", fixture=fixture, models=[])
        server, thread, port = _serve(app)
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
                steps = page.locator("#stage-stepper li")
                assert steps.count() == 6
                if stage == "done":
                    assert page.locator("#stage-stepper li.is-done").count() == 6
                elif stage == "fleet":
                    assert page.locator("#stage-stepper li.is-current").count() == 1
                    assert page.locator("#stage-stepper li.is-done").count() >= 1
                elif expect_failed:
                    assert page.locator("#stage-stepper li.is-failed").count() >= 1
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=5)

    check("fleet")
    check("done")
    check("timed_out", expect_failed=True)

    fixture = _runs_fixture(repo)
    app = create_app(repo, root=ROOT, token="test-token", fixture=fixture, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1024, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            width = page.locator(".sidebar").evaluate("el => getComputedStyle(el).width")
            assert width == "64px"
            assert page.locator(".brand-word").evaluate("el => getComputedStyle(el).display") == "none"

            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(200)
            assert page.locator("#sidebar-toggle").is_visible()
            page.locator("#sidebar-toggle").click()
            page.wait_for_timeout(100)
            assert page.locator(".shell").evaluate("el => el.classList.contains('sidebar-open')")
            assert page.locator("#run-history button").count() == 6
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.ui
def test_placeholder_contrast_and_controls_sheet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", fixture=_runs_fixture(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            from tests.test_theme_claude import _oklch_to_linear_srgb, _parse_oklch_tokens, contrast

            tokens = (ROOT / "src/treefleet/ui/static/css/tokens.css").read_text(encoding="utf-8")
            raw = _parse_oklch_tokens(tokens)
            ratio = contrast(_oklch_to_linear_srgb(*raw["muted-2"]), _oklch_to_linear_srgb(*raw["surface"]))
            assert ratio >= 4.5, f"placeholder contrast {ratio}"
            muted = page.evaluate(
                "() => getComputedStyle(document.documentElement).getPropertyValue('--muted-2').trim()"
            )
            assert muted.startswith("oklch")

            missing = page.request.get(f"http://127.0.0.1:{port}/static/controls-sheet.html")
            assert missing.status == 404

            fixture_html = (ROOT / "tests/fixtures/controls-sheet.html").read_text(encoding="utf-8")
            fixture_html = fixture_html.replace('href="/static/', f'href="http://127.0.0.1:{port}/static/')
            fixture_html = fixture_html.replace('href="/static/icons', f'href="http://127.0.0.1:{port}/static/icons')
            page.set_content(fixture_html, wait_until="networkidle")
            field = page.locator("#sheet-text")
            styles = field.evaluate(
                """el => {
                  const s = getComputedStyle(el);
                  return { h: s.height, r: s.borderRadius };
                }"""
            )
            assert styles["h"] == "36px"
            assert styles["r"] == "6px"
            tabs = page.locator(".tabs .tab")
            t0 = tabs.nth(0).bounding_box()
            t1 = tabs.nth(1).bounding_box()
            assert t0 and t1
            assert 14 <= (t1["x"] - (t0["x"] + t0["width"])) <= 20
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
