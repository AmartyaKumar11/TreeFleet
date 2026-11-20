"""15.D4c file= targeted diff: independent of whole-diff truncation, literal pathspecs."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from treefleet.git.diff_view import build_diff_payload, git_argv_literal

pytestmark = [pytest.mark.phase8, pytest.mark.phase11]


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


def test_file_filter_returns_patch_when_last_file_beyond_whole_diff_cap(tmp_path: Path) -> None:
    """A repo whose full diff exceeds 2MB; file= for the last file returns its patch with truncated=false."""
    repo = tmp_path / "repo"
    _git(repo)

    # Five ~500KB patches → whole > FULL_CAP (2 MB); each single file stays under FULL_CAP.
    for i in range(5):
        content = f"line {i}\n" * 40000
        (repo / f"file{i}.txt").write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add large files"], cwd=repo, check=True, capture_output=True)

    base = _head(repo)

    for i in range(5):
        content = f"modified line {i}\n" * 40000
        (repo / f"file{i}.txt").write_text(content, encoding="utf-8")

    # Whole-diff at FULL_CAP truncates before the last file's patch is fully present.
    whole = build_diff_payload(repo, base, full=True)
    assert whole["truncated"] is True
    # file4 is beyond the capped whole patch (sorted file0..file4).
    assert "diff --git a/file4.txt" not in whole["diff"]

    last_file_payload = build_diff_payload(repo, base, file_filter="file4.txt")
    assert len(last_file_payload["files"]) == 1
    assert last_file_payload["files"][0]["path"] == "file4.txt"
    assert last_file_payload["truncated"] is False
    assert "modified line 4" in last_file_payload["diff"]
    assert last_file_payload["diff"].count("modified line 4") > 1000


def test_file_filter_literal_pathspec_magic_chars(tmp_path: Path) -> None:
    """Pathspec magic characters like :(top), *, [a] are treated literally."""
    repo = tmp_path / "repo"
    _git(repo)

    # Create files with special characters
    special_files = [":(top)x", "*", "[a]"]
    for fname in special_files:
        try:
            (repo / fname).write_text(f"content of {fname}\n", encoding="utf-8")
            subprocess.run(["git", "add", fname], cwd=repo, check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            # Some filesystems don't allow these characters
            pytest.skip(f"Filesystem does not support filename: {fname}")

    subprocess.run(["git", "commit", "-m", "add special files"], cwd=repo, check=True, capture_output=True)
    base = _head(repo)

    # Modify the files
    for fname in special_files:
        (repo / fname).write_text(f"modified {fname}\n", encoding="utf-8")

    # Request each file - should find exact match, not expand magic
    for fname in special_files:
        payload = build_diff_payload(repo, base, file_filter=fname)
        if payload["files"]:
            assert len(payload["files"]) == 1
            assert payload["files"][0]["path"] == fname.replace("\\", "/")
            assert f"modified {fname}" in payload["diff"]


def test_file_filter_nonexistent_returns_empty_safely(tmp_path: Path) -> None:
    """file= with a nonexistent path returns empty files list without crash."""
    repo = tmp_path / "repo"
    _git(repo)
    (repo / "real.txt").write_text("content\n", encoding="utf-8")
    subprocess.run(["git", "add", "real.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add real"], cwd=repo, check=True, capture_output=True)
    base = _head(repo)

    payload = build_diff_payload(repo, base, file_filter="nonexistent.txt")
    assert payload["files"] == []
    assert payload["diff"] == ""
    assert payload["truncated"] is False


def test_file_filter_untracked_uses_no_index_diff(tmp_path: Path) -> None:
    """file= for an untracked file runs the existing --no-index diff."""
    repo = tmp_path / "repo"
    _git(repo)
    base = _head(repo)

    (repo / "untracked.txt").write_text("untracked content\n", encoding="utf-8")

    payload = build_diff_payload(repo, base, file_filter="untracked.txt")
    assert len(payload["files"]) == 1
    assert payload["files"][0]["path"] == "untracked.txt"
    assert payload["files"][0]["status"] == "added"
    assert "untracked content" in payload["diff"]


def test_file_filter_rename_includes_both_paths(tmp_path: Path) -> None:
    """file= for a renamed file includes both old and new paths in the git diff command."""
    repo = tmp_path / "repo"
    _git(repo)
    (repo / "oldname.txt").write_text("same body\n" * 20, encoding="utf-8")
    subprocess.run(["git", "add", "oldname.txt"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "add"], cwd=repo, check=True, capture_output=True)
    base = _head(repo)

    subprocess.run(["git", "mv", "oldname.txt", "newname.txt"], cwd=repo, check=True, capture_output=True)

    payload = build_diff_payload(repo, base, file_filter="newname.txt")
    assert len(payload["files"]) == 1
    assert payload["files"][0]["path"] == "newname.txt"
    assert payload["files"][0]["status"] == "renamed"
    assert payload["files"][0]["old_path"] == "oldname.txt"
    # The diff should show the rename
    assert "rename from oldname.txt" in payload["diff"] or "oldname.txt" in payload["diff"]


def test_git_argv_includes_literal_pathspecs_flag(tmp_path: Path) -> None:
    """file= uses global --literal-pathspecs before the subcommand."""
    argv = git_argv_literal(
        "diff",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--find-renames",
        "-U3",
        "--end-of-options",
        "abc123",
        "--",
        "file.txt",
    )
    assert argv[0] == "git"
    assert argv[1] == "--literal-pathspecs"
    assert "diff" in argv
    assert argv.index("--literal-pathspecs") < argv.index("diff")
    assert "--end-of-options" in argv
    assert argv[-2:] == ["--", "file.txt"]
