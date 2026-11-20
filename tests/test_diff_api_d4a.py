"""15.D4a /api/diff payload: statuses, caps, safety flags, untracked."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from treefleet.git.diff_view import build_diff_payload
from treefleet.ui.server import create_app

pytestmark = pytest.mark.phase8

ROOT = Path(__file__).resolve().parents[1]


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")


def _git(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@local"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _commit(path: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=path, check=True, capture_output=True)


def test_build_diff_payload_statuses_and_untracked(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    (repo / "added.txt").write_text("new\n", encoding="utf-8")
    (repo / "README.md").write_text("hi\nthere\n", encoding="utf-8")
    (repo / "gone.txt").write_text("x\n", encoding="utf-8")
    _commit(repo, "prep")
    base2 = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    (repo / "gone.txt").unlink()
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    (repo / "renamed-old.txt").write_text("r\n", encoding="utf-8")
    _commit(repo, "track")
    subprocess.run(["git", "mv", "renamed-old.txt", "renamed-new.txt"], cwd=repo, check=True, capture_output=True)
    (repo / "untracked.txt").write_text("u\n", encoding="utf-8")
    (repo / "empty.txt").write_text("", encoding="utf-8")
    (repo / "bin.dat").write_bytes(b"\x00\x01\x02\x03")
    payload = build_diff_payload(repo, base2)
    by_path = {item["path"]: item for item in payload["files"]}
    assert "diff" in payload
    assert payload["truncated"] is False
    assert payload["total_bytes"] >= payload["shown_bytes"]
    assert by_path["README.md"]["status"] in {"modified", "mode_changed"}
    assert by_path["gone.txt"]["status"] == "deleted"
    assert by_path["renamed-new.txt"]["status"] in {"renamed", "added", "modified"}
    if by_path["renamed-new.txt"]["status"] == "renamed":
        assert by_path["renamed-new.txt"]["old_path"]
    assert by_path["untracked.txt"]["status"] == "added"
    assert "Untracked:" not in payload["diff"]
    assert "untracked.txt" in payload["diff"] or by_path["untracked.txt"]["binary"] is False
    assert by_path["bin.dat"]["binary"] is True
    assert by_path["empty.txt"]["status"] == "added"
    # base unused; silence lint
    assert base


def test_diff_external_and_textconv_never_run(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    marker = tmp_path / "marker.txt"
    driver = tmp_path / "boom.bat" if os.name == "nt" else tmp_path / "boom.sh"
    if os.name == "nt":
        driver.write_text(f'echo boom> "{marker}"\n', encoding="utf-8")
    else:
        driver.write_text(f'#!/bin/sh\necho boom > "{marker}"\n', encoding="utf-8")
        driver.chmod(0o755)
    subprocess.run(
        ["git", "config", "diff.external", str(driver)],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "diff.foo.textconv", str(driver)],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("changed again\n", encoding="utf-8")
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    build_diff_payload(repo, base)
    assert not marker.exists()


def test_symlink_outside_worktree_skipped(tmp_path: Path) -> None:
    """Skipped on Windows without symlink privilege; see test_resolve_inside_* in d4a2."""
    if os.name == "nt":
        pytest.skip("symlink privileges vary on Windows; containment covered by resolve_inside unit tests")
    repo = tmp_path / "repo"
    _git(repo)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret\n", encoding="utf-8")
    link = repo / "leak"
    link.symlink_to(outside)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    payload = build_diff_payload(repo, base)
    paths = {item["path"] for item in payload["files"]}
    assert "leak" not in paths


def test_api_diff_caps_and_file_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    # Create a worktree-like path registered via fixture board — use repo as worktree through list_worktrees.
    # The API only allows listed worktrees; create_app with fixture still uses real list_worktrees.
    # Register by creating an actual linked worktree.
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", str(wt), "HEAD"], cwd=repo, check=True, capture_output=True)
    big = "x" * 4000 + "\n"
    for i in range(80):
        (wt / f"f{i}.txt").write_text(big * 2, encoding="utf-8")
    app = create_app(repo, root=ROOT, token="test-token", fixture={"repo": str(repo), "tasks": []}, models=[])
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token", "Host": "127.0.0.1"}
    res = client.get("/api/diff", params={"path": str(wt)}, headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert "truncated" in body and "total_bytes" in body and "shown_bytes" in body
    assert isinstance(body["files"], list)
    one = client.get(
        "/api/diff",
        params={"path": str(wt), "file": "f0.txt", "full": 1},
        headers=headers,
    )
    assert one.status_code == 200
    assert isinstance(one.json()["files"], list)
    bad = client.get("/api/diff", params={"path": str(wt), "file": "../secret"}, headers=headers)
    assert bad.status_code == 400
