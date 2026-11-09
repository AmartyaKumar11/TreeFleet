"""Card line counts are this run's committed diff, not another worktree."""

from __future__ import annotations

import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

from treefleet.git.proofs import committed_numstat
from treefleet.ui.server import create_app

pytestmark = pytest.mark.phase8

ROOT = Path(__file__).resolve().parents[1]


def _git(path: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    return proc.stdout.strip()


def _init(path: Path) -> str:
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.email", "t@local")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("one\ntwo\nthree\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "init")
    return _git(path, "rev-parse", "HEAD")


def _numstat(path: Path, base: str) -> list[tuple[str, int, int]]:
    text = _git(path, "diff", "--numstat", f"{base}...HEAD")
    rows: list[tuple[str, int, int]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        added, deleted, name = line.split("\t", 2)
        rows.append((name.replace("\\", "/"), int(added), int(deleted)))
    return rows


def test_committed_numstat_matches_git_and_ignores_uncommitted(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    base = _init(repo)
    _git(repo, "checkout", "-b", "alpha")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_admin.py").write_text("a\n" * 10, encoding="utf-8")
    _git(repo, "add", "tests/test_admin.py")
    _git(repo, "commit", "-m", "admin")
    alpha = [(item.path, item.additions, item.deletions) for item in committed_numstat(repo, base)]
    assert alpha == _numstat(repo, base)
    assert alpha != []
    (repo / "tests" / "scratch.py").write_text("nope\n", encoding="utf-8")
    assert [(item.path, item.additions, item.deletions) for item in committed_numstat(repo, base)] == alpha

    _git(repo, "checkout", "-b", "beta", base)
    (repo / "tests" / "test_notifications.py").write_text("n\n" * 4, encoding="utf-8")
    _git(repo, "add", "tests/test_notifications.py")
    _git(repo, "commit", "-m", "notes")
    beta = [(item.path, item.additions, item.deletions) for item in committed_numstat(repo, base)]
    assert beta == _numstat(repo, base)
    assert beta != alpha

    _git(repo, "checkout", "--detach", base)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    assert committed_numstat(repo, base) == []


def _serve(app: FastAPI) -> tuple[uvicorn.Server, threading.Thread, int]:
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
    assert server.started
    return server, thread, port


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")


def test_cards_use_this_run_and_skip_empty_branches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _init(repo)
    decoy = [
        {"path": "README.md", "additions": 1, "deletions": 3},
        {"path": "marker.txt", "additions": 1, "deletions": 0},
    ]
    fixture = {
        "repo": str(repo),
        "branch": "demo-base",
        "head": "3be485b",
        "clean": True,
        "stage": "fleet",
        "run_id": "fea7ef2e",
        "started_at": time.time(),
        "awaiting": None,
        "events": [],
        "report": None,
        "tasks": [
            {
                "id": "test-admin",
                "title": "Admin",
                "lane": "working",
                "status": "running",
                "branch": "tf/fea7ef2e/test-admin",
                "model": "composer-2.5",
            },
            {
                "id": "test-notifications",
                "title": "Notes",
                "lane": "working",
                "status": "running",
                "branch": "tf/fea7ef2e/test-notifications",
                "model": "composer-2.5",
            },
            {
                "id": "gamma",
                "title": "Gamma",
                "lane": "working",
                "status": "running",
                "branch": "tf/fea7ef2e/gamma",
                "model": "composer-2.5",
                "files": [{"path": "tests/nope.py", "additions": 9, "deletions": 9}],
            },
        ],
        "worktrees": [
            {
                "branch": "tf/29588bb2/test-admin",
                "path": "C:/wt/29588bb2/test-admin",
                "files": decoy,
                "committed": decoy,
            },
            {
                "branch": "tf/29588bb2/test-notifications",
                "path": "C:/wt/29588bb2/test-notifications",
                "files": decoy,
                "committed": decoy,
            },
            {
                "branch": "tf/fea7ef2e/test-admin",
                "path": "C:/wt/fea7ef2e/test-admin",
                "files": decoy,
                "committed": [{"path": "tests/test_admin.py", "additions": 76, "deletions": 0}],
            },
            {
                "branch": "tf/fea7ef2e/test-notifications",
                "path": "C:/wt/fea7ef2e/test-notifications",
                "files": decoy,
                "committed": [{"path": "tests/test_notifications.py", "additions": 33, "deletions": 0}],
            },
            {
                "branch": "tf/fea7ef2e/gamma",
                "path": "C:/wt/fea7ef2e/gamma",
                "files": [{"path": "tests/nope.py", "additions": 9, "deletions": 9}],
                "committed": [],
            },
        ],
    }
    app = create_app(repo, root=ROOT, token="test-token", fixture=fixture, models=[])
    server, thread, port = _serve(app)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            admin = page.locator(".card", has_text="Admin")
            notes = page.locator(".card", has_text="Notes")
            gamma = page.locator(".card", has_text="Gamma")
            admin.wait_for()
            assert admin.locator(".chip.lines").inner_text() == "+76 -0"
            assert notes.locator(".chip.lines").inner_text() == "+33 -0"
            assert gamma.locator(".chip.lines").count() == 0
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
