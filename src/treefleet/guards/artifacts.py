"""Tracked generated files are restored before a commit. Cursor's files stay."""

from __future__ import annotations

from pathlib import Path

from treefleet.git.worktrees import run_git
from treefleet.guards.paths import declared


def tracked_generated(repo: Path) -> list[str]:
    proc = run_git(["ls-files", "-z"], cwd=repo)
    if proc.returncode != 0:
        return []
    names = [item.replace("\\", "/") for item in proc.stdout.split("\0") if item]
    return [name for name in names if name.endswith(".pyc") or "node_modules/" in name]


def revert_undeclared(worktree: Path, allowed: list[str], cursor_files: list[str]) -> list[str]:
    """Restore tracked changes the task did not declare and Cursor did not edit."""
    proc = run_git(["diff", "--name-only", "HEAD"], cwd=worktree)
    changed = [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]
    edited = {item.replace("\\", "/") for item in cursor_files}
    reverted: list[str] = []
    for path in changed:
        if path in edited or declared(path, allowed):
            continue
        run_git(["checkout", "--", path], cwd=worktree)
        reverted.append(path)
    others = run_git(["ls-files", "--others", "--exclude-standard", "-z"], cwd=worktree)
    for raw in others.stdout.split("\0"):
        if not raw:
            continue
        path = raw.replace("\\", "/")
        if path.endswith(".pyc") or "/__pycache__/" in f"/{path}" or "node_modules/" in path:
            target = worktree / raw
            if target.is_file():
                target.unlink()
            reverted.append(path)
    return reverted
