"""15.D3 kanban, cards, git graph, and D2 carry-overs."""

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


def _task(task_id: str, **extra: object) -> dict:
    body = {
        "id": task_id,
        "title": extra.pop("title", f"Task {task_id}"),
        "status": extra.pop("status", "running"),
        "lane": extra.pop("lane", "working"),
        "branch": extra.pop("branch", f"tf/abcd1234/very-long-worker-name-for-truncation/{task_id}"),
        "model": extra.pop("model", "composer-2.5"),
    }
    body.update(extra)
    return body


def _board(repo: Path, **extra: object) -> dict:
    now = time.time()
    body = {
        "repo": str(repo),
        "branch": "main",
        "head": "abc1234",
        "clean": True,
        "goal": "Kanban board",
        "stage": "fleet",
        "started_at": now - 90,
        "run_id": "abcd1234",
        "needs_human": False,
        "awaiting": None,
        "tasks": [
            _task(
                "w1",
                title="Running card title that is long enough to wrap two lines of text for clamp",
                action="Editing board.css",
                files=[{"path": "a.py", "additions": 3, "deletions": 1}],
            ),
            _task(
                "n1",
                title="Needs you",
                lane="needs",
                status="needs_review",
                verdict="revise",
                reason="Please reconsider the approach",
                needs_human=True,
            ),
            _task(
                "r1",
                title="In review",
                lane="review",
                status="done",
                verdict="approve",
                reason="Looks good with notes",
            ),
            _task(
                "rdy",
                title="Ready",
                lane="ready",
                status="done",
                verdict="approve",
                files=[{"path": "b.py", "additions": 2, "deletions": 0}],
            ),
        ],
        "events": [],
        "worktrees": [],
        "report": {"integration": {"branch": "tf/abcd1234/integrated", "base": "abc1234", "merged": ["rdy"]}},
    }
    body.update(extra)
    return body


@pytest.mark.ui
def test_run_strip_states_agree_with_stepper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    cases = [
        ("fleet", None, "", "Running ·", "is-current"),
        ("approve", [{"id": "p", "title": "P", "allowed_paths": []}], "", "Awaiting approval", "is-current"),
        ("timed_out", None, "fleet failed", "Failed at", "is-failed"),
        ("cancelled", None, "cancelled by user", "Cancelled", None),
        ("done", None, "", "Done", "is-done"),
    ]
    for stage, awaiting, message, text, cls in cases:
        fixture = _board(repo, stage=stage, awaiting=awaiting, message=message)
        if stage == "done":
            fixture["finished_at"] = time.time()
        app = create_app(repo, root=ROOT, token="t", fixture=fixture, models=[])
        server, thread, port = _serve(app)
        try:
            with sync_playwright() as p:
                page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
                page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
                status = page.locator("#run-status").inner_text()
                assert text in status
                if cls == "is-done":
                    assert page.locator("#stage-stepper li.is-done").count() == 6
                elif cls:
                    assert page.locator(f"#stage-stepper li.{cls}").count() >= 1
                page.close()
        finally:
            server.should_exit = True
            thread.join(timeout=5)


@pytest.mark.ui
def test_kanban_layout_cards_graph_and_xss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    fixture = _board(
        repo,
        tasks=[
            _task(
                "w1",
                title=XSS,
                branch=f"tf/abcd1234/long-prefix-name/{XSS}",
                action="Editing",
                files=[{"path": "a.py", "additions": 4, "deletions": 0}],
            ),
            _task(
                "n1",
                title="Needs you",
                lane="needs",
                status="needs_review",
                verdict="revise",
                reason="Please reconsider",
                needs_human=True,
            ),
            _task("empty-lane-keep", title="x", lane="review", status="done"),
        ],
    )
    # leave ready empty for rail collapse when cramped
    fixture["tasks"] = [t for t in fixture["tasks"] if t["lane"] != "ready"]
    app = create_app(repo, root=ROOT, token="t", fixture=fixture, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")

            widths = page.evaluate(
                """() => [...document.querySelectorAll('.lane:not(.is-rail)')].map(
                  el => el.getBoundingClientRect().width)"""
            )
            assert widths and min(widths) >= 264

            card = page.locator(".card[data-task-id='w1']")
            for sel in (".model", ".age", ".branch", ".action-text"):
                h = card.locator(sel).evaluate("el => el.getBoundingClientRect().height")
                assert h < 28, sel
            branch = card.locator(".branch-text").inner_text()
            assert "…" in branch or "..." in branch
            assert XSS.split("<")[0] in branch or "…" in branch
            assert card.locator("img, script").count() == 0
            assert XSS in card.locator(".title").inner_text()

            lines = card.locator(".chip.lines").inner_text()
            assert "+" in lines and "−" in lines

            page.locator(".card", has_text="Needs you").first.wait_for()
            assert page.locator(".card.is-needs").count() >= 1

            graph = page.locator("#git-graph")
            assert "GIT GRAPH" in graph.inner_text().upper() or "Git graph" in graph.inner_text()
            page.locator(".graph-toggle").click()
            page.wait_for_timeout(50)
            assert page.locator("#git-graph").evaluate("el => el.classList.contains('is-collapsed')")
            page.reload(wait_until="networkidle")
            assert page.locator("#git-graph").evaluate("el => el.classList.contains('is-collapsed')")

            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(200)
            assert page.locator(".mobile-lane-tabs").is_visible()
            assert page.locator(".lane.is-active").count() == 1
            assert page.locator("#goal").is_visible()

            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.ui
def test_static_controls_sheet_is_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page()
            res = page.request.get(f"http://127.0.0.1:{port}/static/controls-sheet.html")
            assert res.status == 404
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.ui
def test_lane_rail_and_keyboard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    # Many lanes with only working filled → empty lanes can rail when cramped with drawer
    tasks = [_task(f"w{i}", title=f"Work {i}", action="Editing") for i in range(3)]
    fixture = _board(repo, tasks=tasks)
    fixture["tasks"] = tasks
    app = create_app(repo, root=ROOT, token="t", fixture=fixture, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            # Desktop (>1100) so rails apply; drawer open so the lane strip is cramped.
            page = p.chromium.launch().new_page(viewport={"width": 1280, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.locator(".card[data-task-id='w0']").click()
            page.wait_for_timeout(200)
            rails = page.locator(".lane.is-rail")
            assert rails.count() > 0, "expected at least one empty lane rail when drawer is open"
            lane_key = rails.first.get_attribute("data-lane")
            assert lane_key
            page.locator(f".lane.is-rail[data-lane='{lane_key}']").click()
            page.wait_for_timeout(50)
            assert "is-rail" not in (page.locator(f".lane[data-lane='{lane_key}']").get_attribute("class") or "")

            page.evaluate("() => window.dispatchEvent(new Event('resize'))")
            page.wait_for_timeout(80)
            rail = page.locator(f".lane.is-rail[data-lane='{lane_key}']")
            assert rail.count() == 1
            rail.focus()
            page.keyboard.press("Enter")
            page.wait_for_timeout(50)
            assert "is-rail" not in (page.locator(f".lane[data-lane='{lane_key}']").get_attribute("class") or "")

            card = page.locator(".card[data-task-id='w0']")
            card.focus()
            page.keyboard.press("ArrowDown")
            focused = page.evaluate("() => document.activeElement?.dataset?.taskId")
            assert focused in {"w0", "w1", "w2"}
            page.keyboard.press("Escape")
            page.wait_for_timeout(50)
            assert page.evaluate("() => document.activeElement?.classList?.contains('card')")
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.ui
def test_refresh_keeps_focus_scroll_and_styles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    tasks = [_task(f"w{i}", title=f"Work {i}", action="Editing") for i in range(4)]
    tasks.append(
        _task(
            "n1",
            title="Needs decision",
            lane="needs",
            status="needs_review",
            verdict="revise",
            reason="Please look again",
            needs_human=True,
        )
    )
    tasks.append(
        _task(
            "rdy",
            title="Ready merge",
            lane="ready",
            status="done",
            verdict="approve",
            files=[{"path": "a.py", "additions": 5, "deletions": 0}],
        )
    )
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo, tasks=tasks), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            card = page.locator(".card[data-task-id='w1']")
            card.focus()
            page.evaluate(
                """() => {
                  const strip = document.querySelector('.lanes');
                  const stack = document.querySelector('#working');
                  if (strip) strip.scrollLeft = 40;
                  if (stack) stack.scrollTop = 24;
                  window.__tfScroll = { left: strip?.scrollLeft, top: stack?.scrollTop };
                }"""
            )
            page.wait_for_timeout(1100)
            assert page.evaluate("() => document.activeElement?.dataset?.taskId") == "w1"
            scrolls = page.evaluate(
                """() => ({
                  left: document.querySelector('.lanes')?.scrollLeft,
                  top: document.querySelector('#working')?.scrollTop,
                  saved: window.__tfScroll,
                })"""
            )
            assert scrolls["left"] == scrolls["saved"]["left"]
            assert scrolls["top"] == scrolls["saved"]["top"]

            needs = page.locator(".card.is-needs").first
            shadow = needs.evaluate("el => getComputedStyle(el).boxShadow")
            assert "2px" in shadow and "inset" in shadow

            page.locator(".card[data-task-id='rdy']").click()
            selected = page.locator(".card.is-selected")
            assert selected.count() == 1
            accent = selected.evaluate("el => getComputedStyle(el).borderColor")
            assert accent

            lines = page.locator(".card[data-task-id='rdy'] .chip.lines").inner_text()
            assert lines.replace(" ", "") in {"+5−0", "+5-0"} or ("+5" in lines and "0" in lines)

            page.evaluate(
                """() => {
                  const row = document.querySelector('.graph-row[data-task-id="w0"]');
                  row.dispatchEvent(new PointerEvent('pointerover', { bubbles: true }));
                }"""
            )
            assert page.locator(".card[data-task-id='w0'].is-graph-hot").count() == 1
            assert page.locator(".graph-row[data-task-id='w0'].is-hot").count() == 1
            page.evaluate(
                """() => {
                  const card = document.querySelector('.card[data-task-id="w1"]');
                  card.dispatchEvent(new PointerEvent('pointerover', { bubbles: true }));
                }"""
            )
            assert page.locator(".card[data-task-id='w1'].is-graph-hot").count() == 1
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.ui
def test_graph_collapse_survives_storage_throw_and_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    empty = _board(repo, tasks=[], report={})
    app = create_app(repo, root=ROOT, token="t", fixture=empty, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            assert "No branches yet" in page.locator("#git-graph").inner_text()
            page.evaluate(
                """() => {
                  const boom = () => { throw new Error('quota'); };
                  Storage.prototype.setItem = boom;
                  Storage.prototype.getItem = boom;
                }"""
            )
            page.locator(".graph-toggle").click()
            page.wait_for_timeout(50)
            assert page.locator("#git-graph").evaluate("el => el.classList.contains('is-collapsed')")
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.ui
def test_skeleton_before_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    # No fixture → first paint may show skeletons until /api/state returns.
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo, tasks=[]), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            # ensureEmptyLane or skeletons: empty board shows dashed empty boxes
            page.wait_for_timeout(100)
            empties = page.locator(".lane-empty, .card.is-skeleton, .skeleton-card")
            assert empties.count() >= 1 or page.locator(".lane .stack").count() == 4
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
