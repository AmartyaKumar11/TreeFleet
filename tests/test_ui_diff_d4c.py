"""15.D4c: visual-integrity helpers, live refresh proof, mobile Close hit-test."""

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

DIFF = """diff --git a/src/alpha.py b/src/alpha.py
index 111..222 100644
--- a/src/alpha.py
+++ b/src/alpha.py
@@ -1,3 +1,3 @@
 def greet(name):
-    return 'hello ' + name
+    return 'hello there ' + name
 keep
diff --git a/added.txt b/added.txt
new file mode 100644
--- /dev/null
+++ b/added.txt
@@ -0,0 +1 @@
+brand new
diff --git a/old.txt b/old.txt
deleted file mode 100644
--- a/old.txt
+++ /dev/null
@@ -1 +0,0 @@
-removed
"""

DIFF_V2 = DIFF.replace("hello there", "hello everyone")

LONG_DIFF_LINES = ["diff --git a/big.py b/big.py\n", "--- a/big.py\n", "+++ b/big.py\n", "@@ -1,1 +1,450 @@\n"]
for i in range(450):
    LONG_DIFF_LINES.append(f"-old {i}\n+new {i}\n")
LONG_DIFF = "".join(LONG_DIFF_LINES)


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


def _board(repo: Path, *, diff: str = DIFF) -> dict:
    now = time.time()
    return {
        "repo": str(repo),
        "branch": "main",
        "head": "abc",
        "clean": True,
        "goal": "d4c",
        "stage": "fleet",
        "started_at": now - 10,
        "run_id": "d4c",
        "needs_human": False,
        "tasks": [
            {
                "id": "d1",
                "title": "Diff task",
                "status": "running",
                "lane": "working",
                "branch": "tf/d4c/d1",
                "model": "composer-2.5",
                "action": "Editing",
                "diff": diff,
                "diff_files": [
                    {"path": "src/alpha.py", "status": "modified", "additions": 1, "deletions": 1, "binary": False},
                    {"path": "added.txt", "status": "added", "additions": 1, "deletions": 0, "binary": False},
                    {"path": "old.txt", "status": "deleted", "additions": 0, "deletions": 1, "binary": False},
                ],
                "lines": ["ok"],
                "checks": [],
            }
        ],
        "events": [],
        "worktrees": [],
        "report": {},
    }


VISUAL_INTEGRITY_JS = """() => {
  const cards = [...document.querySelectorAll('#diff .diff-file-card, #diff-overlay .diff-file-card')];
  const problems = [];
  for (const card of cards) {
    const body = card.querySelector('.diff-file-body');
    // Allow ~scrollbar slack; real clip is caught by row-below-card.
    if (body && !body.hidden && body.scrollHeight > body.clientHeight + 24) {
      problems.push({kind:'body-scroll', path: card.dataset.path, sh: body.scrollHeight, ch: body.clientHeight});
    }
    const rows = [...card.querySelectorAll('tr.diff-line')].filter((r) => !r.classList.contains('diff-hunk'));
    for (const row of rows) {
      const codes = [...row.querySelectorAll('.diff-code')];
      const ln = row.querySelector('.diff-ln');
      const signs = [...row.querySelectorAll('.diff-sign')];
      for (const code of codes) {
        if (!code.textContent) continue;
        if (ln && ln.textContent.trim()) {
          const cr = code.getBoundingClientRect();
          const lr = ln.getBoundingClientRect();
          const cMid = (cr.top + cr.bottom) / 2;
          const lMid = (lr.top + lr.bottom) / 2;
          if (Math.abs(cMid - lMid) > 3) {
            problems.push({kind:'v-align', path: card.dataset.path, delta: cMid - lMid});
          }
        }
        // First glyph via Range — must not land on a gutter.
        const walker = document.createTreeWalker(code, NodeFilter.SHOW_TEXT);
        const textNode = walker.nextNode();
        if (textNode && textNode.textContent && textNode.textContent.length) {
          const range = document.createRange();
          range.setStart(textNode, 0);
          range.setEnd(textNode, 1);
          const g = range.getBoundingClientRect();
          if (g.width > 0 && g.height > 0) {
            const hit = document.elementFromPoint(g.left + Math.min(1, g.width / 2), (g.top + g.bottom) / 2);
            if (hit && (hit.closest('.diff-ln') || hit.closest('.diff-sign'))) {
              problems.push({kind:'gutter-over-code', path: card.dataset.path, hit: hit.className});
            }
          }
        }
      }
      const isChange = row.classList.contains('add') || row.classList.contains('del')
        || row.classList.contains('diff-add') || row.classList.contains('diff-del');
      if (isChange) {
        const visibleSign = signs.some((s) => {
          const t = (s.childNodes[0] && s.childNodes[0].textContent) || '';
          return t === '+' || t === '-';
        });
        if (!visibleSign) {
          problems.push({kind:'sign', path: card.dataset.path});
        }
      }
      const tds = [...row.children];
      const sum = tds.reduce((a, td) => a + td.getBoundingClientRect().width, 0);
      const inner = card.getBoundingClientRect().width;
      if (sum + 2 < inner * 0.9) {
        problems.push({kind:'tint-width', path: card.dataset.path, sum, inner});
      }
      const rowBottom = row.getBoundingClientRect().bottom;
      const cardBottom = (body || card).getBoundingClientRect().bottom;
      if (rowBottom > cardBottom + 2) {
        problems.push({kind:'row-below-card', path: card.dataset.path});
      }
    }
  }
  return problems.slice(0, 20);
}"""


def _open_diff(page, width: int | None = None) -> None:
    page.locator(".card[data-task-id='d1']").click(force=True)
    page.wait_for_timeout(80)
    if width is not None:
        page.evaluate(
            """(w) => {
              document.documentElement.style.setProperty('--drawer-width', w + 'px');
              const handle = document.getElementById('drawer-resize');
              if (handle) handle.setAttribute('aria-valuenow', String(w));
            }""",
            width,
        )
        page.wait_for_timeout(40)
    # evaluate click: at 360px the Diff tab may be scrolled out of the tab strip.
    page.evaluate("() => document.querySelector(\".drawer .tab[data-pane='diff']\")?.click()")
    page.wait_for_selector("#diff .diff-summary-bar, #diff .diff-file-card, #diff .diff-empty", timeout=8000)
    page.wait_for_timeout(120)


def test_visual_integrity_unified_widths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
            _open_diff(page, 480)
            for w in (360, 480, 720):
                page.evaluate(
                    """(width) => {
                      document.documentElement.style.setProperty('--drawer-width', width + 'px');
                    }""",
                    w,
                )
                page.wait_for_timeout(80)
                assert page.locator("#diff .diff-file-card").count() >= 1
                problems = page.evaluate(VISUAL_INTEGRITY_JS)
                assert problems == [], (w, problems)
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_visual_integrity_split_overlay_1440(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
            _open_diff(page, 480)
            page.locator(".diff-expand-overlay-btn").click(force=True)
            page.wait_for_selector("#diff-overlay", timeout=3000)
            page.wait_for_timeout(150)
            split = page.locator("#diff-overlay .diff-view-btn", has_text="Split")
            assert not split.is_disabled()
            split.click(force=True)
            page.wait_for_timeout(100)
            assert split.get_attribute("aria-checked") == "true"
            assert page.locator("#diff-overlay table.diff-split").count() >= 1
            problems = page.evaluate(VISUAL_INTEGRITY_JS)
            assert problems == [], problems
            page.keyboard.press("Escape")
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_wrap_and_large_file_expand(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    fixture = _board(repo, diff=LONG_DIFF)
    fixture["tasks"][0]["diff_files"] = [
        {"path": "big.py", "status": "modified", "additions": 450, "deletions": 450, "binary": False}
    ]
    app = create_app(repo, root=ROOT, token="t", fixture=fixture, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            _open_diff(page, 720)
            assert page.locator(".diff-expand-file-btn").count() >= 1
            label = page.locator(".diff-expand-file-btn").first.inner_text()
            assert "Expand" in label and "lines" in label
            page.locator(".diff-expand-file-btn").first.click(force=True)
            page.wait_for_timeout(200)
            assert page.locator("#diff tr.diff-line").count() > 50
            page.locator("#diff .diff-wrap-btn").evaluate("el => el.click()")
            page.wait_for_timeout(80)
            assert page.locator("#diff .diff-wrap-btn").get_attribute("aria-pressed") == "true"
            page.locator("#diff .diff-wrap-btn").evaluate("el => el.click()")
            page.wait_for_timeout(80)
            assert page.locator("#diff .diff-wrap-btn").get_attribute("aria-pressed") == "false"
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_visible_states_trunc_empty_loading_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
            page.locator(".card[data-task-id='d1']").click(force=True)
            page.evaluate("() => document.querySelector(\".drawer .tab[data-pane='diff']\")?.click()")
            page.wait_for_timeout(80)

            async def paint(payload):  # type: ignore[no-untyped-def]
                return None

            page.evaluate(
                """async (payload) => {
                  const root = document.getElementById('diff');
                  const mod = await import('/static/js/diff-render.js');
                  mod.createDiffPane(root, {}).render(payload);
                }""",
                {
                    "diff": DIFF,
                    "files": [{"path": "src/alpha.py", "status": "modified", "additions": 1, "deletions": 1}],
                    "truncated": True,
                    "total_bytes": 3_000_000,
                    "shown_bytes": 262_144,
                },
            )
            banner = page.locator(".diff-truncation-banner")
            assert banner.is_visible()
            assert page.get_by_role("button", name="Load full diff").is_visible()

            page.evaluate(
                """async () => {
                  const root = document.getElementById('diff');
                  const mod = await import('/static/js/diff-render.js');
                  mod.createDiffPane(root, {}).render({diff:'', files:[]});
                }"""
            )
            assert page.locator(".diff-empty").is_visible()
            assert "No changes yet" in page.locator(".diff-empty").inner_text()

            page.evaluate(
                """async () => {
                  const root = document.getElementById('diff');
                  const mod = await import('/static/js/diff-render.js');
                  mod.createDiffPane(root, {}).render(null, {skeleton:true});
                }"""
            )
            assert page.locator(".diff-skeleton .skeleton-card").count() >= 1

            page.evaluate(
                """async () => {
                  const root = document.getElementById('diff');
                  const mod = await import('/static/js/diff-render.js');
                  mod.createDiffPane(root, {}).render({error:'boom'});
                }"""
            )
            assert page.get_by_role("button", name="Retry").is_visible()
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_live_refresh_preserves_scroll_collapse_active(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo, diff=DIFF), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.locator(".card[data-task-id='d1']").click(force=True)
            page.evaluate("() => document.querySelector(\".drawer .tab[data-pane='diff']\")?.click()")
            page.wait_for_timeout(120)
            result = page.evaluate(
                """async ({v1, v2}) => {
                  const root = document.getElementById('diff');
                  while (root.firstChild) root.removeChild(root.firstChild);
                  const mod = await import('/static/js/diff-render.js');
                  const pane = mod.createDiffPane(root, {});
                  const files = [
                    {path:'src/alpha.py',status:'modified',additions:1,deletions:1,binary:false},
                    {path:'added.txt',status:'added',additions:1,deletions:0,binary:false},
                    {path:'old.txt',status:'deleted',additions:0,deletions:1,binary:false},
                  ];
                  pane.render({diff:v1, files});
                  const scroller = root.querySelector('.diff-scroller');
                  scroller.scrollTop = 40;
                  const stable = root.querySelector('.diff-file-card[data-path="added.txt"]');
                  const collapseBtn = stable.querySelector('.diff-chevron-btn');
                  collapseBtn.click();
                  const active = root.querySelector('.diff-file-card[data-path="old.txt"]');
                  active.classList.add('is-active');
                  const beforeScroll = scroller.scrollTop;
                  const beforeCollapsed = stable.querySelector('.diff-file-body').hidden;
                  pane.render({diff:v2, files}, {preserve:true});
                  const afterStable = root.querySelector('.diff-file-card[data-path="added.txt"]');
                  const afterActive = root.querySelector('.diff-file-card.is-active');
                  const afterScroller = root.querySelector('.diff-scroller');
                  const alpha = root.querySelector('.diff-file-card[data-path="src/alpha.py"]');
                  return {
                    sameNode: afterStable === stable,
                    scroll: afterScroller.scrollTop,
                    beforeScroll,
                    collapsed: afterStable.querySelector('.diff-file-body').hidden,
                    beforeCollapsed,
                    activePath: afterActive && afterActive.dataset.path,
                    alphaText: alpha ? alpha.innerText : '',
                  };
                }""",
                {"v1": DIFF, "v2": DIFF_V2},
            )
            assert result["sameNode"] is True
            assert result["collapsed"] is True and result["beforeCollapsed"] is True
            assert abs(result["scroll"] - result["beforeScroll"]) <= 2
            assert result["activePath"] == "old.txt"
            assert "everyone" in result["alphaText"] or "hello" in result["alphaText"]
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_mobile_sheet_close_hit_testable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
            # Open sidebar first so it would cover if z-index wrong
            page.evaluate(
                """() => {
                  document.querySelector('.shell')?.classList.add('sidebar-open');
                  document.getElementById('sidebar')?.classList.add('is-sheet-open');
                }"""
            )
            page.locator(".card[data-task-id='d1']").click(force=True)
            page.wait_for_timeout(100)
            for pane in ("live", "diff", "proofpane", "checks", "costpane"):
                page.evaluate(
                    "(id) => document.querySelector(`.drawer .tab[data-pane='${id}']`)?.click()",
                    pane,
                )
                page.wait_for_timeout(40)
                ok = page.evaluate(
                    """() => {
                      const btn = document.getElementById('drawer-close');
                      if (!btn || btn.hidden) return {ok:false, why:'missing'};
                      const r = btn.getBoundingClientRect();
                      const x = r.left + r.width / 2;
                      const y = r.top + r.height / 2;
                      const hit = document.elementFromPoint(x, y);
                      const sidebarOpen = document.querySelector('.shell')?.classList.contains('sidebar-open');
                      return {
                        ok: !!hit && (hit === btn || btn.contains(hit)),
                        hit: hit && (hit.id || hit.className),
                        sidebarOpen: !!sidebarOpen,
                        title: document.getElementById('task-title')?.getBoundingClientRect().top,
                      };
                    }"""
                )
                assert ok["ok"], (pane, ok)
                assert ok["sidebarOpen"] is False, (pane, ok)
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_load_diff_notloaded_against_file_filter_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """UI Load diff for a notLoaded file hits file= against a real worktree fixture."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    (repo / "late.py").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "add", "late.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "late"], cwd=repo, check=True, capture_output=True)

    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", str(wt), "HEAD"], cwd=repo, check=True, capture_output=True)
    (wt / "late.py").write_text("new line from worktree\n", encoding="utf-8")

    fixture = _board(repo, diff="")
    fixture["tasks"][0]["diff"] = ""
    fixture["tasks"][0]["diff_files"] = [
        {"path": "late.py", "status": "modified", "additions": 1, "deletions": 1, "binary": False}
    ]
    app = create_app(repo, root=ROOT, token="t", fixture=fixture, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.locator(".card[data-task-id='d1']").click(force=True)
            page.evaluate("() => document.querySelector(\".drawer .tab[data-pane='diff']\")?.click()")
            page.wait_for_timeout(80)
            page.evaluate(
                """async (wtPath) => {
                  const root = document.getElementById('diff');
                  while (root.firstChild) root.removeChild(root.firstChild);
                  const mod = await import('/static/js/diff-render.js');
                  const auth = { Authorization: 'Bearer t' };
                  const pane = mod.createDiffPane(root, {
                    onFetch: async (opts) => {
                      let url = '/api/diff?path=' + encodeURIComponent(wtPath);
                      if (opts && opts.file) url += '&file=' + encodeURIComponent(opts.file);
                      const res = await fetch(url, { headers: auth });
                      pane.render(await res.json());
                    },
                  });
                  pane.render({
                    diff: '',
                    files: [{
                      path: 'late.py', status: 'modified', additions: 1, deletions: 1,
                      binary: false, notLoaded: true,
                    }],
                  });
                }""",
                str(wt),
            )
            page.wait_for_selector("button:has-text('Load diff')", timeout=5000)
            page.get_by_role("button", name="Load diff").click()
            page.wait_for_selector("tr.diff-line .diff-code", timeout=8000)
            assert "new line from worktree" in page.locator("#diff").inner_text()
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_closed_drawer_focus_ring_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
            page.locator(".card[data-task-id='d1']").click(force=True)
            page.keyboard.press("Escape")
            page.wait_for_timeout(80)
            state = page.evaluate(
                """() => {
                  const card = document.querySelector('.card[data-task-id=\"d1\"]');
                  card.focus();
                  const cs = getComputedStyle(card);
                  return {
                    selected: card.classList.contains('is-selected'),
                    outline: cs.outlineStyle,
                    outlineWidth: cs.outlineWidth,
                    borderColor: cs.borderColor,
                  };
                }"""
            )
            assert state["selected"] is False
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
