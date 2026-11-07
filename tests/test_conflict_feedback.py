"""Merge conflicts become one revise round on the owning task before exclude."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from treefleet.git.merge import conflict_revision_feedback, integrate, merge_tip_into_task
from treefleet.git.worktrees import create_worktree, remove_worktree

pytestmark = pytest.mark.phase6


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        shell=False,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or "git failed")
    return proc.stdout


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.email", "tf@test"], repo)
    _git(["config", "user.name", "tf"], repo)
    (repo / "README.md").write_text("base\nmiddle\nend\n", encoding="utf-8")
    _git(["add", "README.md"], repo)
    _git(["commit", "-m", "base"], repo)
    _git(["branch", "-M", "main"], repo)
    return repo


def _commit(worktree: Path, text: str) -> None:
    (worktree / "README.md").write_text(text, encoding="utf-8")
    _git(["add", "README.md"], worktree)
    _git(["commit", "-m", "edit"], worktree)


def test_conflict_revision_feedback_names_paths_and_tip() -> None:
    text = conflict_revision_feedback(["README.md", "a.py"], "abc1234")
    assert "README.md" in text
    assert "a.py" in text
    assert "abc1234" in text
    assert "conflict" in text.lower()
    assert len(text) <= 2000


def test_merge_tip_into_task_leaves_markers(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    root = tmp_path / "wt"
    root.mkdir()
    run_id = "abcd1234"
    alpha = create_worktree(repo, run_id, "alpha", root, "main")
    beta = create_worktree(repo, run_id, "beta", root, "main")
    try:
        _commit(alpha, "ALPHA\nmiddle\nend\n")
        _commit(beta, "BETA\nmiddle\nend\n")
        tip = _git(["rev-parse", f"tf/{run_id}/alpha"], repo).strip()
        paths = merge_tip_into_task(beta, tip)
        assert paths == ["README.md"]
        body = (beta / "README.md").read_text(encoding="utf-8")
        assert "<<<<<<<" in body
    finally:
        remove_worktree(repo, alpha)
        remove_worktree(repo, beta)


def test_integrate_resolves_conflict_once_then_merges(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    root = tmp_path / "wt"
    root.mkdir()
    run_id = "efgh5678"
    alpha = create_worktree(repo, run_id, "alpha", root, "main")
    beta = create_worktree(repo, run_id, "beta", root, "main")
    made = [alpha, beta, root / run_id / "integrated"]
    calls: list[tuple[str, str, list[str]]] = []
    try:
        _commit(alpha, "ALPHA\nmiddle\nend\n")
        _commit(beta, "BETA\nmiddle\nend\n")

        def resolve_once(task_id: str, tip: str, paths: list[str]) -> bool:
            calls.append((task_id, tip, paths))
            assert task_id == "beta"
            assert "README.md" in paths
            worktree = root / run_id / task_id
            left = merge_tip_into_task(worktree, tip)
            assert left == ["README.md"]
            (worktree / "README.md").write_text("ALPHA\nmiddle\nBETA\n", encoding="utf-8")
            _git(["add", "README.md"], worktree)
            _git(["commit", "-m", "resolve"], worktree)
            return True

        report = integrate(
            repo,
            run_id,
            ["alpha", "beta"],
            {"alpha": f"tf/{run_id}/alpha", "beta": f"tf/{run_id}/beta"},
            "main",
            worktree_root=root,
            resolve_once=resolve_once,
        )
        assert len(calls) == 1
        assert report.merged == ["alpha", "beta"]
        assert report.conflicts == []
        merged = _git(["show", f"{report.branch}:README.md"], repo)
        assert "ALPHA" in merged and "BETA" in merged
    finally:
        for path in made:
            if path.exists():
                try:
                    remove_worktree(repo, path)
                except Exception:
                    pass


def test_second_conflict_excludes_without_second_resolve(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    root = tmp_path / "wt"
    root.mkdir()
    run_id = "ijkl9012"
    alpha = create_worktree(repo, run_id, "alpha", root, "main")
    beta = create_worktree(repo, run_id, "beta", root, "main")
    made = [alpha, beta, root / run_id / "integrated"]
    calls: list[str] = []
    try:
        _commit(alpha, "ALPHA\nmiddle\nend\n")
        _commit(beta, "BETA\nmiddle\nend\n")

        def resolve_once(task_id: str, tip: str, paths: list[str]) -> bool:
            calls.append(task_id)
            worktree = root / run_id / task_id
            merge_tip_into_task(worktree, tip)
            # Abort the tip merge so the task branch is unchanged; retry still conflicts.
            _git(["merge", "--abort"], worktree)
            return True

        report = integrate(
            repo,
            run_id,
            ["alpha", "beta"],
            {"alpha": f"tf/{run_id}/alpha", "beta": f"tf/{run_id}/beta"},
            "main",
            worktree_root=root,
            resolve_once=resolve_once,
        )
        assert calls == ["beta"]
        assert report.merged == ["alpha"]
        assert "beta" not in report.merged
        assert any("beta" in item.between for item in report.conflicts)
        assert any(line.startswith("beta:") and "conflict" in line for line in report.excluded)
    finally:
        for path in made:
            if path.exists():
                try:
                    remove_worktree(repo, path)
                except Exception:
                    pass
