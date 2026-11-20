"""Step 1 parser fixture generator - validate parser against real git diffs."""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from treefleet.git.diff_view import build_diff_payload
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


def _commit(repo: Path, msg: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=repo, check=True, capture_output=True)


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
        "goal": "Fixture test",
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
def test_fixture_simple_add_modify_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate fixture: add, modify, delete files and validate parser."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)

    # Initial commit
    (repo / "keep.txt").write_text("unchanged\n", encoding="utf-8")
    (repo / "modify.txt").write_text("old content\n", encoding="utf-8")
    (repo / "delete.txt").write_text("gone\n", encoding="utf-8")
    _commit(repo, "initial")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Make changes
    (repo / "add.txt").write_text("new file\n", encoding="utf-8")
    (repo / "modify.txt").write_text("new content\n", encoding="utf-8")
    (repo / "delete.txt").unlink()

    payload = build_diff_payload(repo, base_sha)

    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page()
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")

            result = page.evaluate(
                """async (args) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(args.diff, args.files);
            }""",
                {"diff": payload["diff"], "files": payload["files"]},
            )

            files_by_path = {f["path"]: f for f in result["files"]}

            # Validate add
            assert "add.txt" in files_by_path
            add_f = files_by_path["add.txt"]
            assert add_f["status"] == "added"

            # Validate modify
            assert "modify.txt" in files_by_path
            mod_f = files_by_path["modify.txt"]
            assert mod_f["status"] == "modified"
            assert mod_f["additions"] > 0
            assert mod_f["deletions"] > 0

            # Validate delete
            assert "delete.txt" in files_by_path
            del_f = files_by_path["delete.txt"]
            assert del_f["status"] == "deleted"

            # Invariant: additions/deletions match numstat
            for meta_file in payload["files"]:
                parsed = files_by_path.get(meta_file["path"])
                if parsed:
                    assert parsed["additions"] == meta_file["additions"], f"{meta_file['path']}: additions mismatch"
                    assert parsed["deletions"] == meta_file["deletions"], f"{meta_file['path']}: deletions mismatch"
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_fixture_rename_with_spaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate fixture: rename file with spaces."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)

    (repo / "old name.txt").write_text("content\n", encoding="utf-8")
    _commit(repo, "initial")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Rename
    subprocess.run(
        ["git", "mv", "old name.txt", "new name.txt"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    payload = build_diff_payload(repo, base_sha)

    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page()
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")

            result = page.evaluate(
                """async (args) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(args.diff, args.files);
            }""",
                {"diff": payload["diff"], "files": payload["files"]},
            )

            f = result["files"][0]
            assert f["status"] == "renamed"
            assert f["path"] == "new name.txt"
            assert f["oldPath"] == "old name.txt"
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_fixture_mode_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate fixture: mode change."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)

    (repo / "script.sh").write_text("#!/bin/bash\necho hi\n", encoding="utf-8")
    _commit(repo, "initial")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Change mode
    subprocess.run(
        ["git", "update-index", "--chmod=+x", "script.sh"],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    payload = build_diff_payload(repo, base_sha)

    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page()
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")

            result = page.evaluate(
                """async (args) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(args.diff, args.files);
            }""",
                {"diff": payload["diff"], "files": payload["files"]},
            )

            f = result["files"][0]
            assert f["modeOld"] is not None
            assert f["modeNew"] is not None
            assert f["modeOld"] != f["modeNew"]
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_fixture_hunk_line_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate hunk line counts match header unless partial."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)

    (repo / "code.py").write_text("line1\nline2\nline3\nline4\nline5\n", encoding="utf-8")
    _commit(repo, "initial")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Modify middle
    (repo / "code.py").write_text("line1\nchanged\nline3\nadded\nline5\n", encoding="utf-8")

    payload = build_diff_payload(repo, base_sha)

    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page()
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")

            result = page.evaluate(
                """async (args) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(args.diff, args.files);
            }""",
                {"diff": payload["diff"], "files": payload["files"]},
            )

            for f in result["files"]:
                if f.get("partial"):
                    continue
                for h in f["hunks"]:
                    ctx_count = len([row for row in h["lines"] if row["type"] == "ctx"])
                    del_count = len([row for row in h["lines"] if row["type"] == "del"])
                    add_count = len([row for row in h["lines"] if row["type"] == "add"])

                    old_actual = ctx_count + del_count
                    new_actual = ctx_count + add_count

                    assert old_actual == h["oldLines"], (
                        f"{f['path']} hunk old lines: expected {h['oldLines']}, got {old_actual}"
                    )
                    assert new_actual == h["newLines"], (
                        f"{f['path']} hunk new lines: expected {h['newLines']}, got {new_actual}"
                    )
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_fixture_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate fixture: copy file."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)

    (repo / "original.txt").write_text("original\n", encoding="utf-8")
    _commit(repo, "initial")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Copy file (git doesn't have direct copy, simulate with content)
    (repo / "copy.txt").write_text("original\n", encoding="utf-8")

    payload = build_diff_payload(repo, base_sha)

    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page()
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")

            result = page.evaluate(
                """async (args) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(args.diff, args.files);
            }""",
                {"diff": payload["diff"], "files": payload["files"]},
            )

            # Just verify parsing doesn't crash
            assert len(result["files"]) > 0
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_fixture_unicode_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate fixture: unicode in paths."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)

    # Create file with unicode
    unicode_name = "café-🚀.txt"
    (repo / unicode_name).write_text("content\n", encoding="utf-8")
    _commit(repo, "initial")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Modify it
    (repo / unicode_name).write_text("modified\n", encoding="utf-8")

    payload = build_diff_payload(repo, base_sha)

    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page()
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")

            result = page.evaluate(
                """async (args) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(args.diff, args.files);
            }""",
                {"diff": payload["diff"], "files": payload["files"]},
            )

            # Git may C-quote unicode paths
            assert len(result["files"]) > 0
            f = result["files"][0]
            # Just verify it parsed without error
            assert f["path"] is not None
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_fixture_binary_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate fixture: binary file."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)

    (repo / "text.txt").write_text("baseline\n", encoding="utf-8")
    _commit(repo, "initial")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Add binary file
    (repo / "data.bin").write_bytes(b"\x00\x01\x02\xff\xfe")

    payload = build_diff_payload(repo, base_sha)

    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page()
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")

            result = page.evaluate(
                """async (args) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(args.diff, args.files);
            }""",
                {"diff": payload["diff"], "files": payload["files"]},
            )

            files_by_path = {f["path"]: f for f in result["files"]}
            if "data.bin" in files_by_path:
                bin_f = files_by_path["data.bin"]
                assert bin_f["binary"] is True
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_fixture_no_newline_at_eof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate fixture: no newline at end of file."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)

    (repo / "file.txt").write_text("line1\n", encoding="utf-8")
    _commit(repo, "initial")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Remove newline
    (repo / "file.txt").write_text("line1\nline2", encoding="utf-8")

    payload = build_diff_payload(repo, base_sha)

    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page()
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")

            result = page.evaluate(
                """async (args) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(args.diff, args.files);
            }""",
                {"diff": payload["diff"], "files": payload["files"]},
            )

            # Check if noEol flag appears (depends on git output)
            f = result["files"][0]
            # Just verify parsing succeeded
            assert f["path"] == "file.txt"
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_fixture_multiple_hunks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate fixture: file with multiple hunks."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)

    content = "\n".join([f"line{i}" for i in range(1, 101)])  # 100 lines
    (repo / "long.txt").write_text(content + "\n", encoding="utf-8")
    _commit(repo, "initial")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Change lines far apart to trigger multiple hunks
    lines = content.split("\n")
    lines[5] = "changed_line5"
    lines[50] = "changed_line50"
    lines[90] = "changed_line90"
    (repo / "long.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    payload = build_diff_payload(repo, base_sha)

    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page()
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")

            result = page.evaluate(
                """async (args) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(args.diff, args.files);
            }""",
                {"diff": payload["diff"], "files": payload["files"]},
            )

            f = result["files"][0]
            # Likely multiple hunks due to distance
            assert len(f["hunks"]) >= 1

            # Validate line counts for all hunks
            for h in f["hunks"]:
                ctx_count = len([row for row in h["lines"] if row["type"] == "ctx"])
                del_count = len([row for row in h["lines"] if row["type"] == "del"])
                add_count = len([row for row in h["lines"] if row["type"] == "add"])

                assert ctx_count + del_count == h["oldLines"]
                assert ctx_count + add_count == h["newLines"]
    finally:
        server.should_exit = True


@pytest.mark.ui
def test_fixture_empty_file_operations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate fixture: add and delete empty files."""
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)

    (repo / "empty_delete.txt").write_text("", encoding="utf-8")
    _commit(repo, "initial")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Delete empty file, add new empty file
    (repo / "empty_delete.txt").unlink()
    (repo / "empty_add.txt").write_text("", encoding="utf-8")

    payload = build_diff_payload(repo, base_sha)

    app = create_app(repo, root=ROOT, token="t", fixture=_board(repo), models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as p:
            page = p.chromium.launch().new_page()
            page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")

            result = page.evaluate(
                """async (args) => {
                const mod = await import('/static/js/diff-parse.js');
                return mod.parseUnifiedDiff(args.diff, args.files);
            }""",
                {"diff": payload["diff"], "files": payload["files"]},
            )

            # Just verify parsing doesn't crash on empty files
            assert len(result["files"]) > 0
    finally:
        server.should_exit = True
