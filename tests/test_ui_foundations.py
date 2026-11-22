"""15.D1 foundations: self-hosted fonts, no external requests, tokenized literals."""

from __future__ import annotations

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
BOARD_CSS = ROOT / "src" / "treefleet" / "ui" / "static" / "css" / "board.css"

# Selectors/areas owned by later tasks (15.D4b–D6); 15.D4a prefixes were ratcheted out.
# Transcript chrome moved to transcript.css (15.D5b). Remaining board.css allowlist:
# palette/settings (out of scope), stage/mobile chrome, focus outlines, orch footer/composer.
LITERAL_ALLOWLIST_SELECTORS = (
    ".orch-footer",
    ".orch-composer",
    ".command-palette",
    ".palette-",
    ".settings-",
    ".stage-",
    ".mobile-",
    ".view-toggle",
    ".knob",
    ".track",
    ":focus",
    "outline",
)

_SIZE_PROP = re.compile(
    r"(?P<prop>font-size|border-radius|transition(?:-duration)?)\s*:\s*(?P<val>[^;{}]+);",
    re.I,
)
_TOKEN = re.compile(r"var\(--")
_LITERAL_LEN = re.compile(r"\b\d+(?:\.\d+)?(?:px|rem|em|ms|s)\b")


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


def _selector_context(text: str, index: int) -> str:
    depth = 0
    i = index - 1
    while i >= 0:
        ch = text[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth == 0:
                j = i - 1
                while j >= 0 and text[j] not in "{}":
                    j -= 1
                return text[j + 1 : i].strip().splitlines()[-1].strip()
            depth -= 1
        i -= 1
    return ""


def test_board_css_size_literals_are_tokens_or_allowlisted() -> None:
    text = BOARD_CSS.read_text(encoding="utf-8")
    offenders: list[str] = []
    for match in _SIZE_PROP.finditer(text):
        val = match.group("val").strip()
        if _TOKEN.search(val):
            continue
        if not _LITERAL_LEN.search(val):
            continue
        if val in {"0", "none", "inherit", "transparent"}:
            continue
        ctx = _selector_context(text, match.start())
        if any(key in ctx for key in LITERAL_ALLOWLIST_SELECTORS):
            continue
        offenders.append(f"{ctx or '<root>'}: {match.group(0).strip()}")
    assert not offenders, "non-token literals outside allowlist:\n" + "\n".join(offenders[:40])


@pytest.mark.ui
def test_fonts_load_locally_without_external_requests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(
        repo, root=ROOT, token="test-token", fixture={"repo": str(repo), "tasks": [], "events": []}, models=[]
    )
    server, thread, port = _serve(app)
    hosts: set[str] = set()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})

            def on_request(req) -> None:  # type: ignore[no-untyped-def]
                from urllib.parse import urlparse

                hosts.add(urlparse(req.url).hostname or "")

            page.on("request", on_request)
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
            page.wait_for_timeout(300)
            checks = page.evaluate(
                """async () => {
                  await document.fonts.ready;
                  return {
                    geist: document.fonts.check('16px Geist'),
                    mono: document.fonts.check('16px \"Geist Mono\"'),
                    serif: document.fonts.check('16px \"Source Serif 4\"'),
                  };
                }"""
            )
            assert checks["geist"]
            assert checks["mono"]
            assert checks["serif"]
            bad = [h for h in hosts if h not in {"127.0.0.1", "localhost", ""}]
            assert not bad, f"non-local hosts: {bad}"
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
