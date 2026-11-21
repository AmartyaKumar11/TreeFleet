"""15.D4e: scroll-width tints, card borders, tint tokens, skeleton, real-git fixtures."""

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


def _real_git_diff(repo: Path) -> str:
    """Build a multi-file diff from real git so hunk headers match line counts."""
    alpha = repo / "src"
    alpha.mkdir(exist_ok=True)
    (alpha / "alpha.py").write_text(
        "def greet(name):\n    return 'hello ' + name\nkeep\n" + ("x" * 280) + "\n",
        encoding="utf-8",
    )
    (repo / "added.txt").write_text("brand new\n", encoding="utf-8")
    (repo / "old.txt").write_text("removed\n", encoding="utf-8")
    (repo / "big.py").write_text("\n".join(f"old {i}" for i in range(420)) + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)

    (alpha / "alpha.py").write_text(
        "def greet(name):\n    return 'hello there ' + name\nkeep\n" + ("y" * 280) + "\n",
        encoding="utf-8",
    )
    (repo / "added.txt").write_text("brand new\nline2\n", encoding="utf-8")
    (repo / "old.txt").unlink()
    (repo / "big.py").write_text("\n".join(f"new {i}" for i in range(420)) + "\n", encoding="utf-8")
    result = subprocess.run(
        ["git", "diff", "--no-color", "--no-ext-diff", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _board(repo: Path, diff: str) -> dict:
    now = time.time()
    return {
        "repo": str(repo),
        "branch": "main",
        "head": "abc",
        "clean": True,
        "goal": "d4e",
        "stage": "fleet",
        "started_at": now - 10,
        "run_id": "d4e",
        "needs_human": False,
        "tasks": [
            {
                "id": "d1",
                "title": "Diff task",
                "status": "running",
                "lane": "working",
                "branch": "tf/d4e/d1",
                "model": "composer-2.5",
                "action": "Editing",
                "diff": diff,
                "diff_files": [
                    {"path": "src/alpha.py", "status": "modified", "additions": 2, "deletions": 2, "binary": False},
                    {"path": "added.txt", "status": "modified", "additions": 1, "deletions": 0, "binary": False},
                    {"path": "old.txt", "status": "deleted", "additions": 0, "deletions": 1, "binary": False},
                    {"path": "big.py", "status": "modified", "additions": 420, "deletions": 420, "binary": False},
                ],
                "lines": ["ok"],
                "checks": [],
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


_JS_RGB_TO_HSL = """
function cssToRgb(str) {
  const c = document.createElement('canvas');
  c.width = 1; c.height = 1;
  const ctx = c.getContext('2d', {willReadFrequently: true});
  ctx.fillStyle = '#000';
  ctx.fillStyle = str;
  ctx.fillRect(0, 0, 1, 1);
  const d = ctx.getImageData(0, 0, 1, 1).data;
  return [d[0], d[1], d[2]];
}
function rgbToHsl(str) {
  if (!str || str === 'transparent' || str === 'rgba(0, 0, 0, 0)') return null;
  const [R, G, B] = cssToRgb(str);
  let r = R/255, g = G/255, b = B/255;
  const max = Math.max(r,g,b), min = Math.min(r,g,b);
  let h = 0, s = 0, l = (max+min)/2;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    switch (max) {
      case r: h = ((g - b) / d + (g < b ? 6 : 0)); break;
      case g: h = ((b - r) / d + 2); break;
      default: h = ((r - g) / d + 4); break;
    }
    h *= 60;
  }
  return {h, s, l, rgb: [R, G, B]};
}
"""


def test_hunk_header_counts_match_lines_for_real_git_fixtures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    diff = _real_git_diff(repo)
    assert "@@" in diff
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo, diff), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page()
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            bad = page.evaluate(
                """async (diffText) => {
                  const mod = await import('/static/js/diff-parse.js');
                  const parsed = mod.parseUnifiedDiff(diffText);
                  const fails = [];
                  for (const file of parsed.files) {
                    for (const hunk of file.hunks) {
                      let oldN = 0, newN = 0;
                      for (const line of hunk.lines) {
                        if (line.type === 'ctx' || line.type === 'del') oldN++;
                        if (line.type === 'ctx' || line.type === 'add') newN++;
                      }
                      if (oldN !== hunk.oldLines || newN !== hunk.newLines) {
                        fails.push({
                          path: file.path,
                          header: hunk.header,
                          oldLines: hunk.oldLines,
                          newLines: hunk.newLines,
                          countedOld: oldN,
                          countedNew: newN,
                        });
                      }
                    }
                  }
                  return fails;
                }""",
                diff,
            )
            assert bad == [], bad
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_scroll_width_tints_and_sticky_gutters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    diff = _real_git_diff(repo)
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo, diff), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            _open_diff(page)
            # Expand alpha only; ensure wrap off.
            page.evaluate(
                """() => {
                  document.querySelectorAll('#diff .diff-file-card').forEach((card) => {
                    const path = card.dataset.path || '';
                    const btn = card.querySelector('.diff-chevron-btn');
                    const body = card.querySelector('.diff-file-body');
                    const wantOpen = path.includes('alpha');
                    if (wantOpen && body.hidden) btn.click();
                    if (!wantOpen && !body.hidden) btn.click();
                  });
                  const wrap = document.querySelector('#diff .diff-wrap-btn');
                  if (wrap && wrap.getAttribute('aria-pressed') === 'true') wrap.click();
                }"""
            )
            page.wait_for_timeout(120)

            def check(scope: str) -> dict:
                return page.evaluate(
                    """(scope) => {
                      const root = document.querySelector(scope);
                      const body = root.querySelector('.diff-file-card[data-path*="alpha"] .diff-file-body');
                      const table = body.querySelector('table.diff-table');
                      body.scrollLeft = 0;
                      const gutter0 = body.querySelector('.diff-ln').getBoundingClientRect().left;
                      body.scrollLeft = body.scrollWidth;
                      const maxLeft = body.scrollLeft;
                      const gutter1 = body.querySelector('.diff-ln').getBoundingClientRect().left;
                      const br = body.getBoundingClientRect();
                      const rows = [...table.querySelectorAll('tr.diff-line, tr.diff-row')];
                      const rights = rows.map((r) => r.getBoundingClientRect().right);
                      const del = table.querySelector('tr.diff-del, tr.del');
                      const add = table.querySelector('tr.diff-add, tr.add');
                      const hunk = table.querySelector('tr.diff-hunk');
                      function sample(row) {
                        if (!row) return null;
                        const r = row.getBoundingClientRect();
                        const x = Math.min(br.right - 2, r.right - 2);
                        const y = r.top + r.height / 2;
                        const el = document.elementFromPoint(x, y);
                        return el ? getComputedStyle(el).backgroundColor : null;
                      }
                      const sign = body.querySelector('.diff-sign');
                      const hunkLabel = hunk && hunk.querySelector('.diff-hunk-sticky');
                      const sr = sign.getBoundingClientRect();
                      const hr = hunkLabel.getBoundingClientRect();
                      return {
                        maxLeft,
                        gutterStable: Math.abs(gutter0 - gutter1) < 1,
                        rowBeyond: rights.every((right) => right >= br.right - 1.5),
                        delBg: sample(del),
                        addBg: sample(add),
                        hunkBg: sample(hunk),
                        cardBg: getComputedStyle(body.closest('.diff-file-card')).backgroundColor,
                        signInView: sr.left >= br.left - 1 && sr.right <= br.right + 1,
                        hunkInView: hr.left >= br.left - 1 && hr.right <= br.right + 1,
                        tableMin: getComputedStyle(table).minWidth,
                      };
                    }""",
                    scope,
                )

            panel = check("#diff")
            assert panel["maxLeft"] > 0, panel
            assert panel["gutterStable"] is True, panel
            assert panel["rowBeyond"] is True, panel
            assert panel["delBg"] != panel["cardBg"], panel
            assert panel["addBg"] != panel["cardBg"], panel
            assert panel["hunkBg"] != panel["cardBg"], panel
            assert panel["signInView"] is True, panel
            assert panel["hunkInView"] is True, panel

            page.locator(".diff-expand-overlay-btn").evaluate("el => el.click()")
            page.wait_for_selector("#diff-overlay", timeout=3000)
            page.wait_for_timeout(100)
            overlay = check("#diff-overlay")
            assert overlay["maxLeft"] > 0, overlay
            assert overlay["rowBeyond"] is True, overlay
            assert overlay["signInView"] is True, overlay
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_collapsed_card_borders_uniform(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    diff = _real_git_diff(repo)
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo, diff), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            _open_diff(page)
            result = page.evaluate(
                """() => {
                  const cards = [...document.querySelectorAll('#diff .diff-file-card')];
                  // Collapse all non-large first
                  cards.forEach((c) => {
                    const btn = c.querySelector('.diff-chevron-btn');
                    const body = c.querySelector('.diff-file-body');
                    if (!body.hidden) btn.click();
                  });
                  const collapsed = cards[0];
                  // Expand one fully
                  const expanded = cards.find((c) => (c.dataset.path || '').includes('alpha'));
                  expanded.querySelector('.diff-chevron-btn').click();
                  // Large collapsed-with-footer is big.py with expand CTA
                  const withFooter = cards.find((c) => c.querySelector('.diff-expand-file-btn'));
                  function borders(card) {
                    const s = getComputedStyle(card);
                    return {
                      widths: [s.borderTopWidth, s.borderRightWidth, s.borderBottomWidth, s.borderLeftWidth],
                      colors: [s.borderTopColor, s.borderRightColor, s.borderBottomColor, s.borderLeftColor],
                    };
                  }
                  // Loading skeleton borders via synthetic render
                  return {
                    collapsed: borders(collapsed),
                    expanded: borders(expanded),
                    withFooter: withFooter ? borders(withFooter) : null,
                  };
                }"""
            )
            for key in ("collapsed", "expanded"):
                b = result[key]
                assert len(set(b["widths"])) == 1, (key, b)
                assert len(set(b["colors"])) == 1, (key, b)
            if result["withFooter"]:
                b = result["withFooter"]
                assert len(set(b["widths"])) == 1, b
                assert len(set(b["colors"])) == 1, b

            # Pixel sample mid-height left/right of a collapsed card
            page.evaluate(
                """() => {
                  document.querySelectorAll('#diff .diff-file-card').forEach((c) => {
                    const body = c.querySelector('.diff-file-body');
                    const btn = c.querySelector('.diff-chevron-btn');
                    if (!body.hidden && !c.querySelector('.diff-expand-file-btn')) btn.click();
                  });
                }"""
            )
            page.wait_for_timeout(80)
            pix = page.evaluate(
                """() => {
                  const card = document.querySelector('#diff .diff-file-card');
                  const r = card.getBoundingClientRect();
                  const y = r.top + r.height / 2;
                  const leftEl = document.elementFromPoint(r.left + 0.5, y);
                  const rightEl = document.elementFromPoint(r.right - 0.5, y);
                  const border = getComputedStyle(card).borderTopColor;
                  return {
                    border,
                    left: leftEl && getComputedStyle(leftEl).borderLeftColor,
                    right: rightEl && getComputedStyle(rightEl).borderRightColor,
                    leftOk: !!(leftEl && (leftEl === card || card.contains(leftEl))),
                    rightOk: !!(rightEl && (rightEl === card || card.contains(rightEl))),
                  };
                }"""
            )
            assert pix["leftOk"] and pix["rightOk"], pix
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_tint_tokens_hsl_ranges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    diff = _real_git_diff(repo)
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo, diff), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            _open_diff(page)
            page.evaluate(
                """() => {
                  const card = [...document.querySelectorAll('#diff .diff-file-card')]
                    .find((c) => (c.dataset.path || '').includes('alpha'));
                  const body = card.querySelector('.diff-file-body');
                  if (body.hidden) card.querySelector('.diff-chevron-btn').click();
                }"""
            )
            page.wait_for_timeout(80)

            def sample(scope: str, split: bool) -> dict:
                return page.evaluate(
                    """({scope, split}) => {
                      """
                    + _JS_RGB_TO_HSL
                    + """
                      const root = document.querySelector(scope);
                      if (split) {
                        const btn = [...root.querySelectorAll('.diff-view-btn')]
                          .find((b) => b.textContent.trim() === 'Split');
                        if (btn) btn.click();
                      }
                      const card = [...root.querySelectorAll('.diff-file-card')]
                        .find((c) => (c.dataset.path || '').includes('alpha'));
                      const table = card
                        ? card.querySelector('table.diff-table')
                        : root.querySelector('table.diff-table');
                      if (!table) return {error: 'no table'};
                      function cell(sel) {
                        const el = table.querySelector(sel);
                        return el ? rgbToHsl(getComputedStyle(el).backgroundColor) : null;
                      }
                      // Prefer per-side classes — split rows carry both .del and .add,
                      // so tr.add td.diff-code would hit the left (deletion) cell first.
                      const isSplit = table.classList.contains('diff-split');
                      return {
                        delCode: isSplit
                          ? cell('td.diff-side-del.diff-code')
                          : cell('tr.del td.diff-code'),
                        delGut: isSplit
                          ? cell('td.diff-side-del.diff-ln')
                          : cell('tr.del td.diff-ln'),
                        addCode: isSplit
                          ? cell('td.diff-side-add.diff-code')
                          : cell('tr.add td.diff-code'),
                        addGut: isSplit
                          ? cell('td.diff-side-add.diff-ln')
                          : cell('tr.add td.diff-ln'),
                        ctx: cell('tr.diff-line:not(.del):not(.add) td.diff-code'),
                        pad: cell('td.diff-side-pad'),
                      };
                    }""",
                    {"scope": scope, "split": split},
                )

            def assert_hues(samples: dict, *, expect_pad: bool) -> None:
                for key in ("delCode", "delGut"):
                    h = samples[key]
                    assert h is not None, (key, samples)
                    assert h["h"] >= 340 or h["h"] <= 25, (key, h)
                for key in ("addCode", "addGut"):
                    h = samples[key]
                    assert h is not None, (key, samples)
                    assert 90 <= h["h"] <= 170, (key, h)
                if samples["ctx"] is not None:
                    assert samples["ctx"]["s"] < 0.10, samples["ctx"]
                if expect_pad and samples["pad"] is not None:
                    assert samples["pad"]["s"] < 0.10, samples["pad"]

            unified = sample("#diff", False)
            assert_hues(unified, expect_pad=False)

            page.locator(".diff-expand-overlay-btn").evaluate("el => el.click()")
            page.wait_for_selector("#diff-overlay", timeout=3000)
            page.wait_for_timeout(80)
            split_overlay = sample("#diff-overlay", True)
            assert_hues(split_overlay, expect_pad=True)
            page.keyboard.press("Escape")
            page.wait_for_timeout(80)
            page.evaluate("() => document.querySelector('#diff .diff-view-btn:nth-child(2)')?.click()")
            page.wait_for_timeout(80)
            # Force split in panel if supported
            page.evaluate(
                """() => {
                  const btn = [...document.querySelectorAll('#diff .diff-view-btn')]
                    .find((b) => b.textContent.trim() === 'Split');
                  if (btn && !btn.disabled) btn.click();
                }"""
            )
            page.wait_for_timeout(100)
            split_ok = page.evaluate("() => !!document.querySelector('#diff table.diff-split')")
            if split_ok:
                split_panel = sample("#diff", False)
                assert_hues(split_panel, expect_pad=True)
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_skeleton_bars_luminance_vs_pane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo, "diff --git a/a b/a\n"), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.locator(".card[data-task-id='d1']").click(force=True)
            page.wait_for_timeout(80)
            page.evaluate("() => document.querySelector(\".drawer .tab[data-pane='diff']\")?.click()")
            page.wait_for_timeout(80)
            page.evaluate(
                """async () => {
                  const root = document.getElementById('diff');
                  const mod = await import('/static/js/diff-render.js');
                  while (root.firstChild) root.removeChild(root.firstChild);
                  mod.createDiffPane(root, {}).render(null, {skeleton: true});
                }"""
            )
            page.wait_for_timeout(80)
            info = page.evaluate(
                """() => {
                  function cssToRgb(str) {
                    const c = document.createElement('canvas');
                    c.width = 1; c.height = 1;
                    const ctx = c.getContext('2d', {willReadFrequently: true});
                    ctx.fillStyle = '#000';
                    ctx.fillStyle = str;
                    ctx.fillRect(0, 0, 1, 1);
                    return ctx.getImageData(0, 0, 1, 1).data;
                  }
                  function lum(str) {
                    const d = cssToRgb(str);
                    return (0.2126*d[0] + 0.7152*d[1] + 0.0722*d[2]) / 255;
                  }
                  const pane = document.getElementById('diff');
                  const bars = [...pane.querySelectorAll('.diff-skeleton .skeleton-card')];
                  // #diff itself is transparent — compare against the drawer surface token.
                  const probe = document.createElement('div');
                  probe.style.background = 'var(--surface)';
                  pane.appendChild(probe);
                  const paneBg = getComputedStyle(probe).backgroundColor;
                  probe.remove();
                  const paneLum = lum(paneBg);
                  return {
                    count: bars.length,
                    height: pane.querySelector('.diff-skeleton')?.getBoundingClientRect().height || 0,
                    deltas: bars.map((b) => Math.abs(lum(getComputedStyle(b).backgroundColor) - paneLum)),
                    paneBg,
                    barBg: bars[0] && getComputedStyle(bars[0]).backgroundColor,
                  };
                }"""
            )
            # Threshold: skeleton surface must differ from pane surface by ≥ 0.02 luminance.
            assert info["count"] >= 3, info
            assert info["height"] > 100, info
            assert all(d > 0.02 for d in info["deltas"]), info
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_expand_file_button_affordance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    diff = _real_git_diff(repo)
    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo, diff), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            _open_diff(page)
            info = page.evaluate(
                """() => {
                  const btn = document.querySelector('#diff .diff-expand-file-btn');
                  if (!btn) return {ok:false};
                  const r = btn.getBoundingClientRect();
                  const body = btn.closest('.diff-file-body').getBoundingClientRect();
                  return {
                    ok: true,
                    aria: btn.getAttribute('aria-label') || '',
                    hasIcon: !!btn.querySelector('.icon'),
                    fullWidth: r.width >= body.width - 2,
                    tag: btn.tagName,
                  };
                }"""
            )
            assert info["ok"] is True, info
            assert "Expand" in info["aria"] and "lines" in info["aria"], info
            assert info["hasIcon"] is True, info
            assert info["fullWidth"] is True, info
            assert info["tag"] == "BUTTON", info
            page.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
