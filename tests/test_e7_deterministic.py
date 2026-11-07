"""Deterministic E7: harness seeds conflicting branches, then integrate + Cursor resolve."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from treefleet.evals.conflict_seed import seed_conflicting_tasks
from treefleet.git.merge import integrate, merge_tip_into_task
from treefleet.git.worktrees import remove_worktree

pytestmark = pytest.mark.phase9


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
    (repo / "README.md").write_text("# Demo\n\nSmall Python app used for demos.\n", encoding="utf-8")
    _git(["add", "README.md"], repo)
    _git(["commit", "-m", "base"], repo)
    _git(["branch", "-M", "main"], repo)
    return repo


def test_seed_conflicting_tasks_edit_same_lines(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    root = tmp_path / "wt"
    root.mkdir()
    seeded = seed_conflicting_tasks(
        repo,
        run_id="abcd1234",
        worktree_root=root,
        path="README.md",
        alpha_text="# Demo\n\nAlpha owns this sentence about the demo.\n",
        beta_text="# Demo\n\nBeta owns this sentence about the demo.\n",
        base_ref="main",
    )
    try:
        assert seeded.branches == {"alpha": "tf/abcd1234/alpha", "beta": "tf/abcd1234/beta"}
        assert "Alpha owns" in (seeded.worktrees["alpha"] / "README.md").read_text(encoding="utf-8")
        assert "Beta owns" in (seeded.worktrees["beta"] / "README.md").read_text(encoding="utf-8")
        assert seeded.results["alpha"].status == "done"
        assert seeded.results["beta"].status == "done"
        assert seeded.verdicts["alpha"].verdict == "approve"
        assert seeded.verdicts["beta"].verdict == "approve"
    finally:
        for path in seeded.worktrees.values():
            remove_worktree(repo, path)


def test_seeded_conflict_resolves_once_via_callback(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    root = tmp_path / "wt"
    root.mkdir()
    run_id = "efgh5678"
    seeded = seed_conflicting_tasks(
        repo,
        run_id=run_id,
        worktree_root=root,
        path="README.md",
        alpha_text="# Demo\n\nAlpha owns this sentence about the demo.\n",
        beta_text="# Demo\n\nBeta owns this sentence about the demo.\n",
        base_ref="main",
    )
    calls: list[str] = []
    try:

        def resolve_once(task_id: str, tip: str, paths: list[str]) -> bool:
            calls.append(task_id)
            worktree = seeded.worktrees[task_id]
            merge_tip_into_task(worktree, tip)
            (worktree / "README.md").write_text(
                "# Demo\n\nAlpha owns this sentence about the demo.\nBeta owns this sentence about the demo.\n",
                encoding="utf-8",
            )
            _git(["add", "README.md"], worktree)
            _git(["commit", "-m", "resolve"], worktree)
            return True

        report = integrate(
            repo,
            run_id,
            ["alpha", "beta"],
            seeded.branches,
            "main",
            worktree_root=root,
            resolve_once=resolve_once,
        )
        assert calls == ["beta"]
        assert report.merged == ["alpha", "beta"]
        assert report.conflicts == []
        body = _git(["show", f"{report.branch}:README.md"], repo)
        assert "Alpha owns" in body and "Beta owns" in body
    finally:
        for path in [*seeded.worktrees.values(), root / run_id / "integrated"]:
            if path.exists():
                try:
                    remove_worktree(repo, path)
                except Exception:
                    pass
