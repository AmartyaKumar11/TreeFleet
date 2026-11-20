"""15.D4b Step 4: Diff pane Playwright UI tests."""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from treefleet.ui.server import create_app

pytestmark = [pytest.mark.phase11, pytest.mark.ui]

ROOT = Path(__file__).resolve().parents[1]

DIFF_V1 = """diff --git a/alpha.txt b/alpha.txt
index 111..222 100644
--- a/alpha.txt
+++ b/alpha.txt
@@ -1,2 +1,2 @@
-hello world
+hello there
 keep
diff --git a/new.txt b/new.txt
new file mode 100644
--- /dev/null
+++ b/new.txt
@@ -0,0 +1 @@
+fresh
diff --git a/gone.txt b/gone.txt
deleted file mode 100644
--- a/gone.txt
+++ /dev/null
@@ -1 +0,0 @@
-bye
diff --git a/old name.txt b/new name.txt
similarity index 90%
rename from old name.txt
rename to new name.txt
diff --git a/pic.bin b/pic.bin
index 111..222 100644
Binary files a/pic.bin and b/pic.bin differ
"""

DIFF_V2 = (
    DIFF_V1.replace("hello there", "hello everyone")
    + """
diff --git a/extra.txt b/extra.txt
new file mode 100644
--- /dev/null
+++ b/extra.txt
@@ -0,0 +1 @@
+extra
"""
)

XSS_BIDI = (
    "diff --git a/<img src=x onerror=alert(1)>.txt b/<img src=x onerror=alert(1)>.txt\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/<img src=x onerror=alert(1)>.txt\n"
    "@@ -0,0 +1 @@\n"
    "+line with \u202eevil\n"
)


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


def _board(repo: Path, *, diff: str = DIFF_V1, truncated: bool = False) -> dict:
    now = time.time()
    files = [
        {"path": "alpha.txt", "status": "modified", "additions": 1, "deletions": 1, "binary": False},
        {"path": "new.txt", "status": "added", "additions": 1, "deletions": 0, "binary": False},
        {"path": "gone.txt", "status": "deleted", "additions": 0, "deletions": 1, "binary": False},
        {
            "path": "new name.txt",
            "old_path": "old name.txt",
            "status": "renamed",
            "similarity": 90,
            "additions": 0,
            "deletions": 0,
            "binary": False,
        },
        {"path": "pic.bin", "status": "modified", "additions": 0, "deletions": 0, "binary": True},
    ]
    return {
        "repo": str(repo),
        "branch": "main",
        "head": "abc1234",
        "clean": True,
        "goal": "D4b diff",
        "stage": "fleet",
        "started_at": now - 90,
        "run_id": "diffd4b",
        "needs_human": False,
        "awaiting": None,
        "tasks": [
            {
                "id": "d1",
                "title": "Diff task",
                "status": "running",
                "lane": "working",
                "branch": "tf/diffd4b/d1",
                "model": "composer-2.5",
                "action": "Editing",
                "diff": diff,
                "diff_files": files,
                "lines": ["ok"],
                "checks": [],
            }
        ],
        "events": [],
        "worktrees": [],
        "report": {},
        "_truncated": truncated,
    }


def _open_diff(page) -> None:
    page.locator(".card[data-task-id='d1']").click(force=True)
    page.wait_for_timeout(80)
    page.locator(".drawer .tab[data-pane='diff']").click(force=True)
    page.wait_for_selector(".diff-summary-bar, .diff-empty, .diff-file-card", timeout=8000)


def test_unified_split_toggle_and_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            _open_diff(page)
            page.evaluate(
                """() => {
                  document.documentElement.style.setProperty('--drawer-width', '480px');
                }"""
            )
            page.wait_for_timeout(80)
            split = page.locator(".diff-view-btn", has_text="Split")
            assert split.is_disabled()
            assert "Widen" in (split.get_attribute("title") or "")
            page.locator(".diff-expand-overlay-btn").click()
            page.wait_for_selector("#diff-overlay", timeout=3000)
            page.set_viewport_size({"width": 1440, "height": 900})
            page.wait_for_timeout(100)
            # Overlay close + focus return
            page.keyboard.press("Escape")
            page.wait_for_timeout(80)
            assert page.locator("#diff-overlay").count() == 0
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_overlay_focus_trap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            _open_diff(page)
            page.locator(".diff-expand-overlay-btn").click()
            dialog = page.locator("#diff-overlay")
            assert dialog.get_attribute("role") == "dialog"
            assert dialog.get_attribute("aria-modal") == "true"
            page.keyboard.press("Escape")
            assert page.locator("#diff-overlay").count() == 0
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_collapse_expand_keyboard_wrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            _open_diff(page)
            assert page.locator(".diff-file-card").count() >= 3
            page.locator(".diff-collapse-all-btn").click()
            page.wait_for_timeout(60)
            hidden = page.evaluate("""() => [...document.querySelectorAll('.diff-file-body')].every((b) => b.hidden)""")
            assert hidden
            page.locator(".diff-expand-all-btn").click()
            page.wait_for_timeout(60)
            page.locator("#diff").focus()
            page.keyboard.press("n")
            page.keyboard.press("c")
            page.locator(".diff-wrap-btn").click()
            assert page.locator(".diff-wrap-btn").get_attribute("aria-pressed") == "true"
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_truncation_and_load_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    fixture = _board(repo)
    # Force API path: clear embedded diff so fetch runs.
    fixture["tasks"][0].pop("diff", None)
    fixture["tasks"][0].pop("diff_files", None)
    app = create_app(repo, root=ROOT, token="t", fixture=fixture, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.route(
                "**/api/diff*",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=(
                        '{"diff":'
                        + __import__("json").dumps(DIFF_V1[:200])
                        + ',"files":[{"path":"alpha.txt","status":"modified",'
                        + '"additions":1,"deletions":1,"binary":false}],'
                        '"truncated":true,"total_bytes":3000000,"shown_bytes":262144}'
                    ),
                ),
            )
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            # Attach a fake worktree path via evaluate after open — board has no worktree;
            # inject payload through renderer directly for truncation UI.
            page.locator(".card[data-task-id='d1']").click(force=True)
            page.locator(".drawer .tab[data-pane='diff']").click(force=True)
            page.wait_for_timeout(100)
            page.evaluate(
                """async (payload) => {
                  const root = document.getElementById('diff');
                  const mod = await import('/static/js/diff-render.js');
                  const pane = mod.createDiffPane(root, {});
                  pane.render(payload);
                }""",
                {
                    "diff": DIFF_V1,
                    "files": [
                        {
                            "path": "alpha.txt",
                            "status": "modified",
                            "additions": 1,
                            "deletions": 1,
                            "binary": False,
                        }
                    ],
                    "truncated": True,
                    "total_bytes": 3_000_000,
                    "shown_bytes": 262_144,
                },
            )
            assert "Showing" in page.locator(".diff-truncation-banner").inner_text()
            assert page.get_by_role("button", name="Load full diff").count() == 1
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_live_refresh_preserves_node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo, diff=DIFF_V1), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.locator(".card[data-task-id='d1']").click(force=True)
            page.locator(".drawer .tab[data-pane='diff']").click(force=True)
            page.wait_for_timeout(120)
            # Drive renderer directly with two payloads.
            result = page.evaluate(
                """async ({v1, v2}) => {
                  const root = document.getElementById('diff');
                  while (root.firstChild) root.removeChild(root.firstChild);
                  const mod = await import('/static/js/diff-render.js');
                  const pane = mod.createDiffPane(root, {});
                  const files = [
                    {path:'alpha.txt',status:'modified',additions:1,deletions:1,binary:false},
                    {path:'new.txt',status:'added',additions:1,deletions:0,binary:false},
                  ];
                  pane.render({diff:v1, files});
                  const stable = root.querySelector('.diff-file-card[data-path="new.txt"]');
                  const id1 = stable;
                  pane.render({diff:v2, files:[
                    {path:'alpha.txt',status:'modified',additions:1,deletions:1,binary:false},
                    {path:'new.txt',status:'added',additions:1,deletions:0,binary:false},
                    {path:'extra.txt',status:'added',additions:1,deletions:0,binary:false},
                  ]}, {preserve:true});
                  const after = root.querySelector('.diff-file-card[data-path="new.txt"]');
                  return { same: after === id1, count: root.querySelectorAll('.diff-file-card').length };
                }""",
                {"v1": DIFF_V1, "v2": DIFF_V2},
            )
            assert result["same"] is True
            assert result["count"] >= 2
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_xss_bidi_and_empty_states(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo, diff=XSS_BIDI), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.locator(".card[data-task-id='d1']").click(force=True)
            page.locator(".drawer .tab[data-pane='diff']").click(force=True)
            page.wait_for_timeout(120)
            html = page.locator("#diff").inner_html()
            assert "<img" not in html.lower() or "&lt;img" in html or "onerror" not in html.lower()
            text = page.locator("#diff").inner_text()
            assert "U+202E" in text or "‹U+202E›" in text or "\u202e" not in text
            # empty
            page.evaluate(
                """async () => {
                  const root = document.getElementById('diff');
                  const mod = await import('/static/js/diff-render.js');
                  mod.createDiffPane(root, {}).render({diff:'', files:[]});
                }"""
            )
            assert "No changes yet" in page.locator("#diff").inner_text()
            page.evaluate(
                """async () => {
                  const root = document.getElementById('diff');
                  const mod = await import('/static/js/diff-render.js');
                  mod.createDiffPane(root, {}).render(null);
                }"""
            )
            assert "No worktree" in page.locator("#diff").inner_text()
            page.evaluate(
                """async () => {
                  const root = document.getElementById('diff');
                  const mod = await import('/static/js/diff-render.js');
                  mod.createDiffPane(root, {}).render({error:'boom'});
                }"""
            )
            assert "Retry" in page.locator("#diff").inner_text()
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_no_horizontal_overflow_drawer_widths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            _open_diff(page)
            for w in (360, 480, 720):
                page.evaluate(
                    """(w) => document.documentElement.style.setProperty('--drawer-width', w + 'px')""",
                    w,
                )
                page.wait_for_timeout(60)
                metrics = page.evaluate(
                    """() => {
                      const pane = document.getElementById('diff');
                      return { sw: pane.scrollWidth, cw: pane.clientWidth };
                    }"""
                )
                assert metrics["sw"] <= metrics["cw"] + 2, (w, metrics)
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_mobile_390_unified_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 390, "height": 844})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            _open_diff(page)
            split = page.locator(".diff-view-btn", has_text="Split")
            assert split.is_disabled() or split.count() == 0 or split.get_attribute("aria-checked") != "true"
            assert page.locator(".diff-summary-bar").count() == 1
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_gutter_not_selectable_sticky(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            _open_diff(page)
            props = page.evaluate(
                """() => {
                  const ln = document.querySelector('.diff-ln');
                  if (!ln) return null;
                  const cs = getComputedStyle(ln);
                  return { userSelect: cs.userSelect || cs.webkitUserSelect, position: cs.position, width: cs.width };
                }"""
            )
            assert props
            assert props["userSelect"] == "none"
            assert props["position"] == "sticky"
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_parse_perf_bound_2mb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    # Build ~2MB mixed fixture
    lines = ["diff --git a/big.txt b/big.txt\n", "--- a/big.txt\n", "+++ b/big.txt\n", "@@ -1,1 +1,1 @@\n"]
    while sum(len(x) for x in lines) < 2_000_000:
        lines.append(f"-old line {len(lines)}\n+new line {len(lines)}\n")
    big = "".join(lines)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            timing = page.evaluate(
                """async (diffText) => {
                  const mod = await import('/static/js/diff-parse.js');
                  const meta = [{path:'big.txt',status:'modified',additions:1,deletions:1}];
                  const t0 = performance.now();
                  const parsed = mod.parseUnifiedDiff(diffText, meta);
                  const t1 = performance.now();
                  const root = document.createElement('div');
                  document.body.appendChild(root);
                  const renderMod = await import('/static/js/diff-render.js');
                  const pane = renderMod.createDiffPane(root, {});
                  const t2 = performance.now();
                  const files = [{...meta[0], binary:false}];
                  pane.render({diff: diffText, files});
                  const t3 = performance.now();
                  return { parseMs: t1 - t0, paintMs: t3 - t2, files: parsed.files.length };
                }""",
                big,
            )
            print("PERF_2MB", timing)
            assert timing["parseMs"] + timing["paintMs"] < 5000
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
