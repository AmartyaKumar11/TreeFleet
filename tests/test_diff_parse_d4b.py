"""Step 1 parser tests for diff-parse.js - Playwright-based JS module tests."""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from treefleet.ui.server import create_app

pytestmark = pytest.mark.phase11

ROOT = Path(__file__).resolve().parents[1]


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")


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
    return {
        "repo": str(repo),
        "branch": "main",
        "head": "abc1234",
        "clean": True,
        "goal": "Parser test",
        "stage": "fleet",
        "started_at": time.time() - 90,
        "run_id": "test123",
        "needs_human": False,
        "awaiting": None,
        "tasks": [],
        "events": [],
        "worktrees": [],
        "report": {},
    }


@pytest.mark.ui
def test_parse_simple_add(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse simple file addition."""
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

            diff = """diff --git a/new.txt b/new.txt
new file mode 100644
--- /dev/null
+++ b/new.txt
@@ -0,0 +1,2 @@
+line one
+line two"""

            result = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff,
            )

            assert len(result["files"]) == 1
            f = result["files"][0]
            assert f["path"] == "new.txt"
            assert f["status"] == "added"
            assert f["modeNew"] == "100644"
            assert f["additions"] == 2
            assert f["deletions"] == 0
            assert len(f["hunks"]) == 1
            h = f["hunks"][0]
            assert h["oldStart"] == 0
            assert h["oldLines"] == 0
            assert h["newStart"] == 1
            assert h["newLines"] == 2
            assert len(h["lines"]) == 2
            assert h["lines"][0]["type"] == "add"
            assert h["lines"][0]["text"] == "line one"
            assert h["lines"][1]["text"] == "line two"
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_parse_simple_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse file deletion."""
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

            diff = """diff --git a/old.txt b/old.txt
deleted file mode 100644
--- a/old.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-gone
-forever"""

            result = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff,
            )

            f = result["files"][0]
            assert f["path"] == "old.txt"
            assert f["status"] == "deleted"
            assert f["deletions"] == 2
            assert f["additions"] == 0
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_parse_rename_with_spaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse rename with spaces in filename."""
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

            diff = """diff --git a/old name.txt b/new name.txt
similarity index 100%
rename from old name.txt
rename to new name.txt"""

            result = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff,
            )

            f = result["files"][0]
            assert f["path"] == "new name.txt"
            assert f["oldPath"] == "old name.txt"
            assert f["status"] == "renamed"
            assert f["similarity"] == 100
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_parse_cquoted_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse C-quoted paths with tabs, quotes, and octal escapes."""
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

            # Test tab, quote, backslash, and octal
            diff = r"""diff --git "a/file\twith\ttabs.txt" "b/file\twith\ttabs.txt"
new file mode 100644
--- /dev/null
+++ "b/file\twith\ttabs.txt"
@@ -0,0 +1 @@
+content"""

            result = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff,
            )

            f = result["files"][0]
            assert f["path"] == "file\twith\ttabs.txt"

            # Test quotes and backslash
            diff2 = r"""diff --git "a/path\"with\"quotes.txt" "b/path\"with\"quotes.txt"
new file mode 100644
--- /dev/null
+++ "b/path\"with\"quotes.txt"
@@ -0,0 +1 @@
+content"""

            result2 = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff2,
            )

            f2 = result2["files"][0]
            assert f2["path"] == 'path"with"quotes.txt'

            # Test octal escape (space is \040)
            diff3 = r"""diff --git "a/file\040name.txt" "b/file\040name.txt"
new file mode 100644
--- /dev/null
+++ "b/file\040name.txt"
@@ -0,0 +1 @@
+content"""

            result3 = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff3,
            )

            f3 = result3["files"][0]
            assert f3["path"] == "file name.txt"
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_parse_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse copy operation."""
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

            diff = """diff --git a/src.txt b/dst.txt
similarity index 95%
copy from src.txt
copy to dst.txt
--- a/src.txt
+++ b/dst.txt
@@ -1,2 +1,2 @@
 keep
-change
+changed"""

            result = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff,
            )

            f = result["files"][0]
            assert f["status"] == "copied"
            assert f["oldPath"] == "src.txt"
            assert f["path"] == "dst.txt"
            assert f["similarity"] == 95
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_parse_mode_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse mode change."""
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

            diff = """diff --git a/script.sh b/script.sh
old mode 100644
new mode 100755
--- a/script.sh
+++ b/script.sh
@@ -1 +1 @@
-#!/bin/bash
+#!/bin/sh"""

            result = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff,
            )

            f = result["files"][0]
            assert f["modeOld"] == "100644"
            assert f["modeNew"] == "100755"
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_parse_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse binary file."""
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

            diff = """diff --git a/img.png b/img.png
new file mode 100644
Binary files /dev/null and b/img.png differ"""

            result = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff,
            )

            f = result["files"][0]
            assert f["binary"] is True
            assert len(f["hunks"]) == 0
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_parse_git_binary_patch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse GIT binary patch and skip content."""
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

            diff = """diff --git a/data.bin b/data.bin
new file mode 100644
GIT binary patch
literal 5
Mc%Z9K0Z9xY008|~^8f$<
literal 0
HcmV?d00001

diff --git a/next.txt b/next.txt
new file mode 100644
--- /dev/null
+++ b/next.txt
@@ -0,0 +1 @@
+after binary"""

            result = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff,
            )

            assert len(result["files"]) == 2
            f0 = result["files"][0]
            assert f0["path"] == "data.bin"
            assert f0["binary"] is True
            f1 = result["files"][1]
            assert f1["path"] == "next.txt"
            assert f1["binary"] is False
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_parse_no_newline_at_eof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse missing newline markers."""
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

            # No newline after deletion
            diff = """diff --git a/a.txt b/a.txt
--- a/a.txt
+++ b/a.txt
@@ -1,2 +1 @@
 keep
-remove
\\ No newline at end of file"""

            result = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff,
            )

            f = result["files"][0]
            assert f["noEolOld"] is True
            assert f["noEolNew"] is False
            assert f["hunks"][0]["lines"][1]["noEol"] is True

            # No newline after addition
            diff2 = """diff --git a/b.txt b/b.txt
--- a/b.txt
+++ b/b.txt
@@ -1 +1,2 @@
 keep
+add
\\ No newline at end of file"""

            result2 = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff2,
            )

            f2 = result2["files"][0]
            assert f2["noEolNew"] is True
            assert f2["noEolOld"] is False
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_parse_crlf_detection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse CRLF line endings - hadCR flag."""
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

            # Line with CRLF
            diff = "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n-old\r\n+new\n"

            result = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff,
            )

            h = result["files"][0]["hunks"][0]
            assert h["lines"][0]["hadCR"] is True
            assert h["lines"][0]["text"] == "old"  # \r stripped
            assert h["lines"][1]["hadCR"] is False
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_parse_hunk_zero_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse hunk with @@ -0,0 +1 @@ (omitted oldLines)."""
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

            diff = """diff --git a/empty.txt b/empty.txt
new file mode 100644
--- /dev/null
+++ b/empty.txt
@@ -0,0 +1 @@
+single line"""

            result = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff,
            )

            h = result["files"][0]["hunks"][0]
            assert h["oldStart"] == 0
            assert h["oldLines"] == 0
            assert h["newStart"] == 1
            assert h["newLines"] == 1
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_parse_hunk_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse section after second @@."""
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

            diff = """diff --git a/code.py b/code.py
--- a/code.py
+++ b/code.py
@@ -10,3 +10,3 @@ def my_function():
 line1
-old
+new"""

            result = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff,
            )

            h = result["files"][0]["hunks"][0]
            assert h["section"] == "def my_function():"
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_parse_unicode_astral_combining(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse unicode including astral and combining characters."""
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

            # Emoji (astral) and combining diacritics
            diff = """diff --git a/unicode.txt b/unicode.txt
--- a/unicode.txt
+++ b/unicode.txt
@@ -1 +1 @@
-Hello 🌍
+Héllo 🚀 café"""

            result = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff,
            )

            h = result["files"][0]["hunks"][0]
            assert "🌍" in h["lines"][0]["text"]
            assert "🚀" in h["lines"][1]["text"]
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_sanitize_bidi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test sanitizeBidi replaces bidi control characters."""
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

            # Test various bidi characters
            result = page.evaluate("""async () => {
                const mod = await import('/static/js/diff-parse.js');
                const testCases = [
                    { input: 'hello\\u202Aworld', expected: 'hello‹U+202A›world' },
                    { input: 'test\\u202Eend', expected: 'test‹U+202E›end' },
                    { input: '\\u2066nested\\u2069', expected: '‹U+2066›nested‹U+2069›' },
                ];
                return testCases.map(tc => ({
                    input: tc.input,
                    expected: tc.expected,
                    actual: mod.sanitizeBidi(tc.input),
                    pass: mod.sanitizeBidi(tc.input) === tc.expected
                }));
            }""")

            for tc in result:
                assert tc["pass"], f"sanitizeBidi({tc['input']}) = {tc['actual']}, expected {tc['expected']}"
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_parse_combined_diff_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse combined diff - should store as raw with notice."""
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

            diff = """diff --git a/merged.txt b/merged.txt
@@@ -1,3 -1,3 +1,3 @@@
  common
- left
 -right
++both"""

            result = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff,
            )

            f = result["files"][0]
            assert f["notice"] == "Combined diffs are not supported"
            assert f["raw"] is not None
            assert len(f["hunks"]) == 0
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_parse_truncation_mid_hunk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse truncated diff - last file marked partial."""
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

            # Hunk expects 5 lines but only 2 provided
            diff = """diff --git a/incomplete.txt b/incomplete.txt
--- a/incomplete.txt
+++ b/incomplete.txt
@@ -1,5 +1,5 @@
+one
+two"""

            result = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff,
            )

            f = result["files"][0]
            assert f["partial"] is True
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_parse_files_meta_merge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse with filesMeta - metadata overrides parser."""
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

            diff = """diff --git a/file.txt b/file.txt
--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+new"""

            filesMeta = [
                {
                    "path": "file.txt",
                    "status": "modified",
                    "binary": False,
                    "additions": 10,  # Override parser count
                    "deletions": 5,
                }
            ]

            result = page.evaluate(
                """async (args) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(args.diff, args.meta);
            }""",
                {"diff": diff, "meta": filesMeta},
            )

            f = result["files"][0]
            assert f["additions"] == 10  # From filesMeta
            assert f["deletions"] == 5
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_parse_files_meta_not_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse filesMeta-only entries marked notLoaded."""
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

            diff = ""

            filesMeta = [
                {
                    "path": "missing.txt",
                    "status": "added",
                    "additions": 100,
                    "deletions": 0,
                    "too_large": True,
                }
            ]

            result = page.evaluate(
                """async (args) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(args.diff, args.meta);
            }""",
                {"diff": diff, "meta": filesMeta},
            )

            assert len(result["files"]) == 1
            f = result["files"][0]
            assert f["notLoaded"] is True
            assert f["tooLarge"] is True
            assert f["path"] == "missing.txt"
            assert len(f["hunks"]) == 0
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_word_diff_basic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test wordDiff tokenization and LCS."""
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

            result = page.evaluate("""async () => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.wordDiff('hello world', 'hello earth');
            }""")

            # Should have segments with changed markers
            assert any(seg["changed"] for seg in result)
            assert any(not seg["changed"] for seg in result)

            # "hello " should be unchanged
            unchanged = [seg for seg in result if not seg["changed"]]
            assert any("hello" in seg["text"] for seg in unchanged)
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_word_diff_skip_threshold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test wordDiff skips when line too long or too many tokens."""
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

            # Test line length > 2000
            result = page.evaluate("""async () => {
                const mod = await import('/static/js/diff-parse.js');
                const longLine = 'x'.repeat(2001);
                return mod.wordDiff(longLine, longLine + 'y');
            }""")

            # Should return single unchanged segment (skip)
            assert len(result) == 1
            assert result[0]["changed"] is False

            # Test token count > 500
            result2 = page.evaluate("""async () => {
                const mod = await import('/static/js/diff-parse.js');
                const manyTokens = 'word '.repeat(501);
                return mod.wordDiff(manyTokens, manyTokens + 'extra');
            }""")

            # Should skip and return as-is
            assert len(result2) == 1
            assert result2[0]["changed"] is False
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_parse_tabs_in_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse tabs in diff content."""
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

            diff = "diff --git a/code.txt b/code.txt\n--- a/code.txt\n+++ b/code.txt\n@@ -1 +1 @@\n-\told\n+\tnew\n"

            result = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff,
            )

            h = result["files"][0]["hunks"][0]
            assert h["lines"][0]["text"] == "\told"
            assert h["lines"][1]["text"] == "\tnew"
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_parse_very_long_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse very long lines without error."""
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

            long_content = "x" * 5000
            diff = f"""diff --git a/long.txt b/long.txt
--- a/long.txt
+++ b/long.txt
@@ -1 +1 @@
-{long_content}
+{long_content}y"""

            result = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff,
            )

            h = result["files"][0]["hunks"][0]
            assert len(h["lines"][0]["text"]) == 5000
            assert len(h["lines"][1]["text"]) == 5001
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_parse_empty_file_add(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse empty file addition."""
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

            diff = """diff --git a/empty.txt b/empty.txt
new file mode 100644
--- /dev/null
+++ b/empty.txt"""

            result = page.evaluate(
                """async (diffText) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(diffText);
            }""",
                diff,
            )

            f = result["files"][0]
            assert f["path"] == "empty.txt"
            assert f["status"] == "added"
            assert len(f["hunks"]) == 0
    finally:
        server.should_exit = True
