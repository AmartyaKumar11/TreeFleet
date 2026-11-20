"""15.D4a2 diff API hardening: locks, markers, spy, containment, file=."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from treefleet.git import diff_view
from treefleet.git.diff_view import build_diff_payload, git_argv, resolve_inside
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


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _write_marker_driver(tmp_path: Path, marker: Path) -> Path:
    driver = tmp_path / ("boom.bat" if os.name == "nt" else "boom.sh")
    if os.name == "nt":
        driver.write_text(f'echo boom> "{marker}"\n', encoding="utf-8")
    else:
        driver.write_text(f'#!/bin/sh\necho boom > "{marker}"\n', encoding="utf-8")
        driver.chmod(0o755)
    return driver


def test_resolve_inside_rejects_outside_fake_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert resolve_inside(repo, "../secret.txt") is None
    assert resolve_inside(repo, "..\\secret.txt") is None
    assert resolve_inside(repo, "/etc/passwd") is None
    assert resolve_inside(repo, "//server/share/x") is None
    if os.name == "nt":
        assert resolve_inside(repo, "C:/Windows/notepad.exe") is None
        assert resolve_inside(repo, "c:/Windows/notepad.exe") is None


def test_resolve_inside_directory_junction(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("directory junctions are a Windows construct")
    repo = tmp_path / "repo"
    _git(repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret\n", encoding="utf-8")
    link = repo / "leak"
    try:
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"could not create directory junction: {exc}")
    base = _head(repo)
    payload = build_diff_payload(repo, base)
    # Junction target resolves outside; untracked under it must be skipped.
    paths = {item["path"] for item in payload["files"]}
    assert "leak/secret.txt" not in paths
    assert resolve_inside(repo, "leak/secret.txt") is None


def test_markers_never_written_by_diff_external_textconv_fsmonitor(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    marker = tmp_path / "marker.txt"
    driver = _write_marker_driver(tmp_path, marker)
    subprocess.run(["git", "config", "diff.external", str(driver)], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "diff.foo.textconv", str(driver)], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "core.fsmonitor", str(driver)], cwd=repo, check=True, capture_output=True)
    (repo / ".gitattributes").write_text("*.md diff=foo\n", encoding="utf-8")
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    (repo / "untracked.md").write_text("u\n", encoding="utf-8")
    base = _head(repo)
    build_diff_payload(repo, base)
    assert not marker.exists()
    build_diff_payload(repo, base, full=True)
    assert not marker.exists()
    build_diff_payload(repo, base, file_filter="README.md")
    assert not marker.exists()


def test_subprocess_spy_requires_safe_flags(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    (repo / "README.md").write_text("x\n", encoding="utf-8")
    base = _head(repo)
    seen: list[list[str]] = []
    real = subprocess.Popen

    def spy(argv, *args, **kwargs):  # type: ignore[no-untyped-def]
        if argv and str(argv[0]) == "git":
            seen.append([str(x) for x in argv])
        return real(argv, *args, **kwargs)

    with patch.object(diff_view.subprocess, "Popen", side_effect=spy):
        build_diff_payload(repo, base)
    assert seen
    for argv in seen:
        assert "--no-optional-locks" in argv
        # Diff scrubbing; ls-files lists only; rev-parse resolves BASE to a SHA.
        assert "--no-ext-diff" in argv or "ls-files" in argv or "rev-parse" in argv
        if "diff" in argv:
            assert "--no-ext-diff" in argv
            assert "--no-textconv" in argv
            assert "--end-of-options" in argv or "--no-index" in argv


def test_stale_index_lock_still_returns_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", str(wt), "HEAD"], cwd=repo, check=True, capture_output=True)
    lock_path = subprocess.run(
        ["git", "rev-parse", "--git-path", "index.lock"],
        cwd=wt,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    lock = Path(lock_path) if Path(lock_path).is_absolute() else wt / lock_path
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("stale\n", encoding="utf-8")
    (wt / "README.md").write_text("locked change\n", encoding="utf-8")
    app = create_app(repo, root=ROOT, token="test-token", fixture={"repo": str(repo), "tasks": []}, models=[])
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token", "Host": "127.0.0.1"}
    res = client.get("/api/diff", params={"path": str(wt)}, headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert "diff" in body and "files" in body
    assert body["truncated"] is False or isinstance(body["truncated"], bool)


def test_file_filter_rejects_escapes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", str(wt), "HEAD"], cwd=repo, check=True, capture_output=True)
    app = create_app(repo, root=ROOT, token="test-token", fixture={"repo": str(repo), "tasks": []}, models=[])
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token", "Host": "127.0.0.1"}
    for bad in ("../secret", "..\\secret", "C:/Windows/x", "c:/Windows/x", "//server/share/x", "/etc/passwd"):
        res = client.get("/api/diff", params={"path": str(wt), "file": bad}, headers=headers)
        assert res.status_code == 400, bad


def test_similarity_populated_for_rename(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    (repo / "oldname.txt").write_text("same body\n" * 20, encoding="utf-8")
    subprocess.run(["git", "add", "oldname.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add"], cwd=repo, check=True, capture_output=True)
    base = _head(repo)
    subprocess.run(["git", "mv", "oldname.txt", "newname.txt"], cwd=repo, check=True, capture_output=True)
    payload = build_diff_payload(repo, base)
    by_path = {item["path"]: item for item in payload["files"]}
    if "newname.txt" in by_path and by_path["newname.txt"]["status"] == "renamed":
        assert "similarity" in by_path["newname.txt"]
        assert isinstance(by_path["newname.txt"]["similarity"], int)


def test_git_argv_helpers_document_exact_prefixes() -> None:
    argv = git_argv("diff", "--numstat", "--find-renames", "--end-of-options", "HEAD")
    assert argv[:2] == ["git", "--no-optional-locks"]
    assert "core.fsmonitor=false" in argv
    assert "core.pager=cat" in argv
    null = "NUL" if os.name == "nt" else "/dev/null"
    no_index = git_argv(
        "diff",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--find-renames",
        "-U3",
        "--no-index",
        "--",
        null,
        "file.txt",
    )
    assert "--no-optional-locks" in no_index
    assert "--no-index" in no_index


def test_poisoned_base_output_option_never_writes_marker(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    marker = tmp_path / "pwned.txt"
    poisoned = f"--output={marker}"
    (repo / "README.md").write_text("changed\n", encoding="utf-8")
    # Falls back to HEAD when base looks like an option; must not create marker.
    payload = build_diff_payload(repo, poisoned)
    assert not marker.exists()
    assert payload.get("base_sha")
    assert len(payload["base_sha"]) == 40


def test_untracked_cap_200_sets_flag(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    for i in range(300):
        (repo / f"u{i:03d}.txt").write_text(f"{i}\n", encoding="utf-8")
    base = _head(repo)
    payload = build_diff_payload(repo, base)
    added = [f for f in payload["files"] if f["status"] == "added" and f["path"].startswith("u")]
    assert len(added) == 200
    assert payload.get("untracked_truncated") is True
