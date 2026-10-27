"""Worktree lifecycle. Every git command is a subprocess with a timeout."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

_MAX_PATH = 120


class GitError(RuntimeError):
    """A git command failed or a repository is not usable."""


@dataclass(frozen=True)
class Worktree:
    path: Path
    head: str
    branch: str | None


def run_git(args: list[str], cwd: Path | None = None, timeout: float = 30) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
        check=False,
    )


def git_out(args: list[str], cwd: Path, timeout: float = 30) -> str:
    proc = run_git(args, cwd=cwd, timeout=timeout)
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or proc.stdout.strip() or "git failed")
    return proc.stdout


def _same(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))


def _configure(repo: Path) -> None:
    git_out(["config", "--local", "core.autocrlf", "false"], repo)
    if run_git(["config", "--local", "user.email"], cwd=repo).returncode != 0:
        git_out(["config", "--local", "user.email", "treefleet@local"], repo)
    if run_git(["config", "--local", "user.name"], cwd=repo).returncode != 0:
        git_out(["config", "--local", "user.name", "TreeFleet"], repo)


def prepare_repo(path: Path, clone_from: Path | None = None) -> Path:
    """Clone or open a repository and refuse a dirty worktree."""
    if clone_from is not None:
        proc = run_git(["clone", "-c", "core.autocrlf=false", str(clone_from), str(path)], timeout=60)
        if proc.returncode != 0:
            raise GitError(proc.stderr.strip() or "git clone failed")
    elif not (path / ".git").exists():
        raise GitError(f"{path} is not a git repository")
    _configure(path)
    status = git_out(["status", "--porcelain"], path)
    if status.strip():
        raise GitError("repository is not clean")
    return path


def branch_name(run_id: str, task_id: str) -> str:
    return f"tf/{run_id}/{task_id}"


def create_worktree(repo: Path, run_id: str, task_id: str, root: Path, base_ref: str = "HEAD") -> Path:
    path = root / run_id / task_id
    if len(str(path)) >= _MAX_PATH:
        raise GitError(f"worktree path is {len(str(path))} characters and must stay under {_MAX_PATH}")
    path.parent.mkdir(parents=True, exist_ok=True)
    proc = run_git(["worktree", "add", "-b", branch_name(run_id, task_id), str(path), base_ref], cwd=repo, timeout=60)
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or "git worktree add failed")
    return path


def list_worktrees(repo: Path) -> list[Worktree]:
    text = git_out(["worktree", "list", "--porcelain"], repo)
    found: list[Worktree] = []
    path: str | None = None
    head: str | None = None
    branch: str | None = None

    def flush() -> None:
        nonlocal path, head, branch
        if path and head:
            short = branch.removeprefix("refs/heads/") if branch else None
            found.append(Worktree(path=Path(path), head=head, branch=short))
        path = head = branch = None

    for line in text.splitlines():
        if not line.strip():
            flush()
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            if path:
                flush()
            path = value
        elif key == "HEAD":
            head = value
        elif key == "branch":
            branch = value
        elif key == "detached":
            branch = None
    if path:
        flush()
    return found


def _registered(repo: Path, worktree: Path) -> bool:
    if not worktree.exists():
        return False
    return any(_same(item.path, worktree) for item in list_worktrees(repo))


def kill_tree(pid: int) -> None:
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = [*root.children(recursive=True), root]
    for proc in procs:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(procs, timeout=5)


def remove_worktree(repo: Path, worktree: Path, pid: int | None = None) -> None:
    """Force-remove a worktree after its process tree is gone. Retry while Windows locks the directory."""
    if pid is not None:
        kill_tree(pid)
    last = ""
    for delay in (0, 0.2, 0.5, 1.0):
        if delay:
            time.sleep(delay)
        if not worktree.exists() and not _registered(repo, worktree):
            run_git(["worktree", "prune"], cwd=repo)
            return
        proc = run_git(["worktree", "remove", "--force", str(worktree)], cwd=repo, timeout=60)
        if proc.returncode == 0:
            return
        last = proc.stderr.strip()
    raise GitError(last or "git worktree remove failed")
