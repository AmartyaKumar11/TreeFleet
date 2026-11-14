"""Claude theme tokens: primary terracotta, violet needs-you, no blue/hardcoded literals."""

from __future__ import annotations

import math
import re
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
STATIC = ROOT / "src" / "treefleet" / "ui" / "static"
TOKENS = STATIC / "css" / "tokens.css"
BOARD_CSS = STATIC / "css" / "board.css"
CONTROLS_CSS = STATIC / "css" / "controls.css"
UI_JS = (STATIC / "js").glob("*.js")

_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_RGB = re.compile(r"rgba?\(")
_BLUE_VAR = re.compile(r"var\(\s*--blue\b")
_BLUE_TOKEN = re.compile(r"--blue\b")
_OLD_BLUE = "#4c8dff"
_HIGHLIGHT = re.compile(r"--highlight\b")

_OKLCH = re.compile(
    r"--(?P<name>[a-z0-9-]+):\s*oklch\(\s*(?P<L>[\d.]+)\s+(?P<C>[\d.]+)\s+(?P<H>[\d.]+)\s*\)",
)


def _oklch_to_linear_srgb(L: float, C: float, H: float) -> tuple[float, float, float]:
    h = math.radians(H)
    a = C * math.cos(h)
    b = C * math.sin(h)
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    ll, mm, ss = l_**3, m_**3, s_**3
    r = 4.0767416621 * ll - 3.3077115913 * mm + 0.2309699292 * ss
    g = -1.2684380046 * ll + 2.6097574011 * mm - 0.3413193965 * ss
    b2 = -0.0041960863 * ll - 0.7034186147 * mm + 1.7076147010 * ss
    return r, g, b2


def _rel_lum(rgb: tuple[float, float, float]) -> float:
    rr, gg, bb = (max(0.0, min(1.0, c)) for c in rgb)
    return 0.2126 * rr + 0.7152 * gg + 0.0722 * bb


def contrast(c1: tuple[float, float, float], c2: tuple[float, float, float]) -> float:
    lum1, lum2 = _rel_lum(c1), _rel_lum(c2)
    hi, lo = max(lum1, lum2), min(lum1, lum2)
    return (hi + 0.05) / (lo + 0.05)


def _parse_oklch_tokens(text: str) -> dict[str, tuple[float, float, float]]:
    out: dict[str, tuple[float, float, float]] = {}
    for match in _OKLCH.finditer(text):
        out[match.group("name")] = (
            float(match.group("L")),
            float(match.group("C")),
            float(match.group("H")),
        )
    return out


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


def _theme_fixture(repo: Path) -> dict[str, object]:
    return {
        "repo": str(repo),
        "branch": "main",
        "head": "abc1234",
        "clean": True,
        "goal": "Theme tuning fixture",
        "worker_model": "router",
        "stage": "fleet",
        "started_at": time.time() - 90,
        "run_id": "abcd1234",
        "needs_human": True,
        "awaiting": None,
        "tasks": [
            {
                "id": "alpha",
                "title": "Parser tests",
                "status": "running",
                "lane": "working",
                "branch": "tf/abcd1234/alpha",
                "model": "composer-2.5",
                "action": "Editing tests/test_parser.py",
                "lines": ["Editing tests/test_parser.py"],
                "diff": "diff --git a/src/a.py b/src/a.py\n+++ b/src/a.py\n@@ -1 +1,2 @@\n+alpha\n",
                "diff_files": [{"path": "src/a.py", "additions": 1, "deletions": 0}],
            },
            {
                "id": "needs1",
                "title": "Needs task",
                "status": "failed",
                "lane": "needs",
                "verdict": "reject",
                "reason": "no new diff",
                "branch": "tf/abcd1234/needs1",
            },
            {
                "id": "review1",
                "title": "Review task",
                "status": "done",
                "lane": "review",
                "verdict": "revise",
                "branch": "tf/abcd1234/review1",
            },
            {
                "id": "ready1",
                "title": "Ready task",
                "status": "done",
                "lane": "ready",
                "verdict": "approve",
                "branch": "tf/abcd1234/ready1",
                "files": [{"path": "README.md", "additions": 1, "deletions": 0}],
            },
        ],
        "worktrees": [],
        "events": [],
        "report": {
            "goal": "Theme tuning fixture",
            "integration": {
                "branch": "tf/abcd1234/integrated",
                "merged": ["ready1"],
                "excluded": [],
                "base": "abc1234",
            },
        },
    }


def test_no_blue_or_hardcoded_colors_outside_tokens() -> None:
    tokens = TOKENS.read_text(encoding="utf-8")
    assert _OLD_BLUE not in tokens
    assert not _BLUE_TOKEN.search(tokens)
    assert not _HIGHLIGHT.search(tokens)
    assert "--accent-strong: oklch(0.5583 0.1276 42.9956)" in tokens
    assert "--status-needs: oklch(0.6898 0.1581 290.4107)" in tokens

    board = BOARD_CSS.read_text(encoding="utf-8")
    controls = CONTROLS_CSS.read_text(encoding="utf-8")
    assert _OLD_BLUE not in board
    assert not _BLUE_VAR.search(board)
    assert not _BLUE_TOKEN.search(board)
    assert not _HIGHLIGHT.search(board)
    assert "var(--accent-strong)" in controls
    assert not _HEX.search(board), f"hex left in board.css: {_HEX.findall(board)}"
    assert not _RGB.search(board), f"rgb left in board.css: {_RGB.findall(board)}"

    for path in UI_JS:
        text = path.read_text(encoding="utf-8")
        assert _OLD_BLUE not in text, path.name
        assert not _BLUE_VAR.search(text), path.name
        assert not _BLUE_TOKEN.search(text), path.name
        hexes = [h for h in _HEX.findall(text) if re.fullmatch(r"#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?", h)]
        assert not hexes, f"hex left in {path.name}: {hexes}"
        assert not _RGB.search(text), path.name


def test_claude_token_contrast_meets_wcag_aa() -> None:
    text = TOKENS.read_text(encoding="utf-8")
    raw = _parse_oklch_tokens(text)

    def lin(name: str) -> tuple[float, float, float]:
        if name == "status-running":
            return _oklch_to_linear_srgb(*raw["accent"])
        if name not in raw:
            raise AssertionError(f"missing oklch token --{name}")
        return _oklch_to_linear_srgb(*raw[name])

    surface = lin("surface")
    bg = lin("bg")
    white = (1.0, 1.0, 1.0)

    assert contrast(lin("text"), bg) >= 4.5
    assert contrast(lin("text"), surface) >= 4.5
    assert contrast(lin("muted"), bg) >= 4.5
    assert contrast(lin("muted"), surface) >= 4.5
    assert contrast(lin("accent"), surface) >= 4.5

    strong_ratio = contrast(white, lin("accent-strong"))
    assert strong_ratio >= 4.5, f"white on accent-strong {strong_ratio:.2f}"

    needs_text = contrast(lin("status-needs"), surface)
    assert needs_text >= 4.5, f"status-needs on surface {needs_text:.2f}"
    needs_dot = contrast(lin("status-needs"), bg)
    assert needs_dot >= 3.0, f"status-needs on bg {needs_dot:.2f}"

    for status in ("status-running", "status-success", "status-warning", "status-error", "status-needs"):
        ratio = contrast(lin(status), surface)
        assert ratio >= 4.5, f"{status} text contrast {ratio:.2f}"

    for status in ("status-running", "status-success", "status-warning", "status-error", "status-needs"):
        for surf_name, surf in (("surface", surface), ("bg", bg), ("surface-2", lin("surface-2"))):
            ratio = contrast(lin(status), surf)
            assert ratio >= 3.0, f"{status} dot on {surf_name}: {ratio:.2f}"


@pytest.mark.ui
def test_run_button_uses_accent_strong_fill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", fixture=_theme_fixture(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            colors = page.evaluate(
                """() => {
                  const root = getComputedStyle(document.documentElement);
                  const btn = getComputedStyle(document.getElementById('run'));
                  return { token: root.getPropertyValue('--accent-strong').trim(), bg: btn.backgroundColor };
                }"""
            )
            token = page.evaluate(
                """() => {
                  const probe = document.createElement('div');
                  probe.style.background = getComputedStyle(document.documentElement)
                    .getPropertyValue('--accent-strong').trim();
                  document.body.appendChild(probe);
                  const resolved = getComputedStyle(probe).backgroundColor;
                  probe.remove();
                  return resolved;
                }"""
            )
            assert colors["token"] == "oklch(0.5583 0.1276 42.9956)"
            assert colors["bg"] == token
            assert "255, 255, 255" not in colors["bg"] and colors["bg"] != "rgb(255, 255, 255)"
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@pytest.mark.ui
def test_orchestrator_reason_stays_inside_verdict_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", fixture=_theme_fixture(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for width, height in ((1440, 900), (390, 844)):
                page = browser.new_page(viewport={"width": width, "height": height})
                page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
                page.get_by_role("tab", name="Orchestrator").click()
                row = page.locator(".verdict-row", has_text="Needs task").first
                row.wait_for()
                reason = row.locator(".reason-chip")
                assert reason.count() == 1
                assert "no new diff" in reason.inner_text()
                boxes = page.evaluate(
                    """() => {
                      const row = [...document.querySelectorAll('.verdict-row')]
                        .find((el) => el.textContent.includes('Needs task'));
                      const reason = row && row.querySelector('.reason-chip');
                      if (!row || !reason) return null;
                      const a = row.getBoundingClientRect();
                      const b = reason.getBoundingClientRect();
                      return {
                        inside:
                          b.top >= a.top - 1 &&
                          b.left >= a.left - 1 &&
                          b.bottom <= a.bottom + 1 &&
                          b.right <= a.right + 1,
                        rowTop: a.top,
                        reasonTop: b.top,
                      };
                    }"""
                )
                assert boxes and boxes["inside"], boxes
                page.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
