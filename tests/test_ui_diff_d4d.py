"""15.D4d: bidi pills, split per-side tints, summary overflow, legacy CSS guard, mobile topbar."""

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

BIDI = "\u202e"
DIFF = f"""diff --git a/src/alpha.py b/src/alpha.py
--- a/src/alpha.py
+++ b/src/alpha.py
@@ -1,3 +1,3 @@
 def greet(name):
-    return 'hello ' + name
+    return 'hello there ' + name
 keep
diff --git a/bidi.txt b/bidi.txt
--- a/bidi.txt
+++ b/bidi.txt
@@ -1,1 +1,1 @@
-plain
+{BIDI}exe.tpp
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

INVISIBLE_POINTS = [
    0x202A,
    0x202B,
    0x202C,
    0x202D,
    0x202E,
    0x2066,
    0x2067,
    0x2068,
    0x2069,
    0x061C,
    0x200E,
    0x200F,
    0x200B,
    0x200C,
    0x200D,
    0x2060,
    0xFEFF,
]

_JS_SANITIZE_UNIT = """async () => {
  const mod = await import('/static/js/safe-text.js');
  const samples = [
    '\\u202Eexe.tpp',
    'a\\u200B\\u200Cb',
    'x\\uFEFFy',
    '\\u2066nest\\u2069',
  ];
  const rawRe = /[\\u202A-\\u202E\\u2066-\\u2069\\u061C\\u200E\\u200F\\u200B-\\u200D\\u2060\\uFEFF]/;
  return samples.map((s) => ({
    in: [...s].map((c) => c.codePointAt(0).toString(16)),
    out: mod.sanitizeBidi(s),
    hasRaw: rawRe.test(mod.sanitizeBidi(s)),
  }));
}"""

_JS_SCAN_INVISIBLES = """(points) => {
  const roots = [
    document.querySelector('.drawer'),
    document.getElementById('diff-overlay'),
  ].filter(Boolean);
  const hits = [];
  const re = new RegExp(
    '[' + points.map((p) => '\\\\u' + p.toString(16).padStart(4, '0')).join('') + ']'
  );
  for (const root of roots) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      if (re.test(node.nodeValue || '')) {
        hits.push({kind: 'text', sample: (node.nodeValue || '').slice(0, 40)});
      }
    }
    for (const el of root.querySelectorAll('[title],[aria-label]')) {
      for (const attr of ['title', 'aria-label']) {
        const v = el.getAttribute(attr);
        if (v && re.test(v)) hits.push({kind: attr, sample: v.slice(0, 40)});
      }
    }
  }
  return hits.slice(0, 10);
}"""

_JS_SPLIT_TINTS = """() => {
  const overlay = document.getElementById('diff-overlay');
  const table = overlay.querySelector('table.diff-split');
  const alpha = overlay.querySelector('.diff-file-card[data-path="src/alpha.py"]');
  const added = overlay.querySelector('.diff-file-card[data-path="added.txt"]');
  const deleted = overlay.querySelector('.diff-file-card[data-path="old.txt"]');
  function bg(el) { return getComputedStyle(el).backgroundColor; }
  function firstRow(card, pred) {
    return [...card.querySelectorAll('tr.diff-line')].find(pred);
  }
  const modRow = firstRow(
    alpha,
    (r) => r.querySelector('.diff-side-del') && r.querySelector('.diff-side-add')
  );
  const addRow = firstRow(added, (r) => r.querySelector('.diff-side-add'));
  const delRow = firstRow(deleted, (r) => r.querySelector('.diff-side-del'));
  const leftDel = modRow.querySelector('.diff-side-del.diff-code');
  const rightAdd = modRow.querySelector('.diff-side-add.diff-code');
  const leftPad = addRow.querySelector(
    '.diff-side-pad.diff-code-old, .diff-code-old.diff-side-pad'
  );
  const rightPad = delRow.querySelector(
    '.diff-side-pad.diff-code-new, .diff-code-new.diff-side-pad'
  );
  const markLeft = leftDel.querySelector('.diff-word-highlight');
  const markRight = rightAdd.querySelector('.diff-word-highlight');
  return {
    hasTable: !!table,
    leftDelBg: bg(leftDel),
    rightAddBg: bg(rightAdd),
    leftPadBg: leftPad ? bg(leftPad) : null,
    rightPadBg: rightPad ? bg(rightPad) : null,
    sameSides: bg(leftDel) === bg(rightAdd),
    markLeft: markLeft ? bg(markLeft) : null,
    markRight: markRight ? bg(markRight) : null,
    marksSame: markLeft && markRight ? bg(markLeft) === bg(markRight) : null,
  };
}"""

_JS_SUMMARY_DRAWER = """() => {
  const pane = document.getElementById('diff');
  const pr = pane.getBoundingClientRect();
  const sel = '.diff-wrap-btn, .diff-collapse-toggle, .diff-expand-overlay-btn, .diff-view-btn';
  const btns = [...pane.querySelectorAll(sel)];
  const bad = [];
  for (const btn of btns) {
    const r = btn.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) { bad.push('zero'); continue; }
    const out =
      r.left < pr.left - 1 ||
      r.right > pr.right + 1 ||
      r.top < pr.top - 1 ||
      r.bottom > pr.bottom + 40;
    if (out) { bad.push({cls: btn.className, r, pr}); continue; }
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (!hit || !(hit === btn || btn.contains(hit))) {
      bad.push({hit: hit && hit.className, cls: btn.className});
    }
  }
  return bad.slice(0, 5);
}"""

_JS_SUMMARY_OVERLAY = """() => {
  const pane =
    document.querySelector('#diff-overlay .diff-overlay-panel') ||
    document.getElementById('diff-overlay');
  const pr = pane.getBoundingClientRect();
  const sel =
    '#diff-overlay .diff-wrap-btn, #diff-overlay .diff-collapse-toggle, #diff-overlay .diff-view-btn';
  const btns = [...document.querySelectorAll(sel)];
  const bad = [];
  for (const btn of btns) {
    const r = btn.getBoundingClientRect();
    if (r.left < pr.left - 1 || r.right > pr.right + 1) bad.push(btn.className);
    const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    if (!hit || !(hit === btn || btn.contains(hit))) bad.push('hit:' + btn.className);
  }
  return bad;
}"""

_JS_LEGACY_CSS = """() => {
  const bad = [];
  for (const sheet of [...document.styleSheets]) {
    let rules;
    try { rules = sheet.cssRules; } catch (e) { continue; }
    for (const rule of rules || []) {
      if (!rule.selectorText || !rule.style) continue;
      if (!/\\.diff-line\\b/.test(rule.selectorText)) continue;
      const display = (rule.style.display || '').trim();
      if (display === 'grid' || display === 'flex') {
        bad.push({selector: rule.selectorText, display, href: sheet.href || 'inline'});
      }
    }
  }
  return bad;
}"""

_JS_MOBILE_TOPBAR = """() => {
  const title = document.getElementById('title');
  const gear = document.querySelector(
    '.settings-wrap button, .topbar [aria-label="Settings"]'
  );
  const more = document.querySelector('.topbar-more-btn');
  const tr = title.getBoundingClientRect();
  const style = getComputedStyle(title);
  const actions = [gear, more].filter(Boolean);
  const rects = actions.map((el) => el.getBoundingClientRect());
  const mid = (r) => (r.top + r.bottom) / 2;
  const sameRow =
    rects.length > 0 &&
    rects.every((r) => Math.abs(mid(r) - mid(tr)) < 20);
  return {
    ellipsis:
      style.textOverflow === 'ellipsis' &&
      (title.scrollWidth > title.clientWidth + 1 || tr.width > 40),
    sameRow,
    titleW: tr.width,
    actionCount: actions.length,
    tops: [tr.top, ...rects.map((r) => r.top)],
  };
}"""


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


def _board(repo: Path) -> dict:
    now = time.time()
    return {
        "repo": str(repo / ("long-project-name-" + ("x" * 24))),
        "branch": f"main{BIDI}evil",
        "head": "abc",
        "clean": True,
        "goal": "d4d",
        "stage": "fleet",
        "started_at": now - 10,
        "run_id": "d4d",
        "needs_human": False,
        "tasks": [
            {
                "id": "d1",
                "title": f"Diff{BIDI} task",
                "status": "running",
                "lane": "working",
                "branch": f"tf/d4d/d1{BIDI}",
                "model": "composer-2.5",
                "action": "Editing",
                "diff": DIFF,
                "diff_files": [
                    {"path": "src/alpha.py", "status": "modified", "additions": 1, "deletions": 1, "binary": False},
                    {"path": "bidi.txt", "status": "modified", "additions": 1, "deletions": 1, "binary": False},
                    {"path": "added.txt", "status": "added", "additions": 1, "deletions": 0, "binary": False},
                    {"path": "old.txt", "status": "deleted", "additions": 0, "deletions": 1, "binary": False},
                ],
                "lines": [f"running {BIDI}exe.tpp", "ok"],
                "checks": [
                    {
                        "name": "pytest",
                        "passed": False,
                        "exit_code": 1,
                        "duration_s": 0.1,
                        "source": "worker",
                        "output": f"fail {BIDI}hidden",
                    }
                ],
            }
        ],
        "events": [],
        "worktrees": [],
        "report": {},
    }


def _open_diff(page) -> None:
    page.locator(".card[data-task-id='d1']").click(force=True)
    page.wait_for_timeout(80)
    page.evaluate("() => document.querySelector(\".drawer .tab[data-pane='diff']\")?.click()")
    page.wait_for_selector("#diff .diff-summary-bar", timeout=8000)
    page.wait_for_timeout(100)


def test_sanitize_bidi_unit_in_browser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            out = page.evaluate(_JS_SANITIZE_UNIT)
            assert all(not row["hasRaw"] for row in out)
            assert any("U+202E" in row["out"] for row in out)
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_no_raw_invisible_chars_in_drawer_or_overlay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
            page.wait_for_timeout(80)
            for pane in ("live", "diff", "proofpane", "checks"):
                page.evaluate(
                    "(id) => document.querySelector(`.drawer .tab[data-pane='${id}']`)?.click()",
                    pane,
                )
                page.wait_for_timeout(60)
            page.evaluate("() => document.querySelector(\".drawer .tab[data-pane='diff']\")?.click()")
            page.wait_for_timeout(80)
            page.locator(".diff-expand-overlay-btn").evaluate("el => el.click()")
            page.wait_for_selector("#diff-overlay", timeout=3000)
            bad = page.evaluate(_JS_SCAN_INVISIBLES, INVISIBLE_POINTS)
            assert bad == [], bad
            pills = page.locator(".bidi-pill").count()
            assert pills >= 1
            page.keyboard.press("Escape")
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_split_tints_per_side(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
            page.locator(".diff-expand-overlay-btn").evaluate("el => el.click()")
            page.wait_for_selector("#diff-overlay", timeout=3000)
            page.locator("#diff-overlay .diff-view-btn", has_text="Split").click(force=True)
            page.wait_for_timeout(120)
            result = page.evaluate(_JS_SPLIT_TINTS)
            assert result["hasTable"]
            assert result["leftDelBg"] != result["rightAddBg"]
            assert result["sameSides"] is False
            assert result["leftPadBg"] is not None and result["rightPadBg"] is not None
            if result["marksSame"] is not None:
                assert result["marksSame"] is False
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_summary_controls_inside_pane_widths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
            for w in (320, 360, 480, 640, 720, 1000):
                page.evaluate(
                    "(width) => document.documentElement.style.setProperty('--drawer-width', width + 'px')",
                    w,
                )
                page.wait_for_timeout(80)
                ok = page.evaluate(_JS_SUMMARY_DRAWER)
                assert ok == [], (w, ok)
            page.locator(".diff-expand-overlay-btn").evaluate("el => el.click()")
            page.wait_for_selector("#diff-overlay", timeout=3000)
            for vw in (1440, 1000):
                page.set_viewport_size({"width": vw, "height": 900})
                page.wait_for_timeout(100)
                ok = page.evaluate(_JS_SUMMARY_OVERLAY)
                assert ok == [], (vw, ok)
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_no_legacy_diff_line_grid_or_flex_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            offenders = page.evaluate(_JS_LEGACY_CSS)
            assert offenders == [], offenders
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_mobile_topbar_long_name_keeps_actions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    fixture = _board(repo)
    fixture["repo"] = str(repo / ("abcdefghijklmnopqrstuvwxyz0123456789xxxx"))  # 40+ chars leaf
    app = create_app(repo, root=ROOT, token="t", fixture=fixture, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            for w in (390, 320):
                page = p.chromium.launch().new_page(viewport={"width": w, "height": 844})
                page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
                page.wait_for_timeout(80)
                state = page.evaluate(_JS_MOBILE_TOPBAR)
                assert state["actionCount"] >= 1, state
                assert state["sameRow"] is True, state
                assert state["ellipsis"] is True, state
                page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
