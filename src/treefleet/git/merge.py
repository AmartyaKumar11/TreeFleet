"""Merge approved task branches in plan order. Conflicts are recorded and the merge is aborted."""

from __future__ import annotations

import os
from pathlib import Path

from treefleet.checks import run_command
from treefleet.contracts import CheckResult, IntegrationConflict, IntegrationReport
from treefleet.git.worktrees import GitError, branch_name, create_worktree, git_out, run_git


def _unmerged(repo: Path) -> list[str]:
    proc = run_git(["-c", "core.quotepath=false", "diff", "--name-only", "--diff-filter=U"], cwd=repo)
    if proc.returncode != 0:
        return []
    return [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]


def _default_root() -> Path:
    path = Path(r"C:\tf") if os.name == "nt" else Path("/tmp/tf")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _checks(worktree: Path, checks: dict[str, str]) -> list[CheckResult]:
    found: list[CheckResult] = []
    for name, command in checks.items():
        code, out, err = run_command(command, worktree)
        found.append(
            CheckResult(
                name=name,  # type: ignore[arg-type]
                command=command[:300],
                exit_code=code,
                passed=code == 0,
                stdout_tail=out[-4000:],
                stderr_tail=err[-4000:],
            )
        )
    return found


def integrate(
    repo: Path,
    run_id: str,
    order: list[str],
    branches: dict[str, str],
    base_ref: str,
    *,
    worktree_root: Path | None = None,
    checks: dict[str, str] | None = None,
    reasons: dict[str, str] | None = None,
) -> IntegrationReport:
    """Merge into tf/<run>/integrated in its own worktree. The caller's checkout is not moved."""
    why = reasons or {}
    root = worktree_root if worktree_root is not None else _default_root()
    user_branch = git_out(["rev-parse", "--abbrev-ref", "HEAD"], repo).strip()
    main_head = git_out(["rev-parse", "HEAD"], repo).strip()
    branch = branch_name(run_id, "integrated")
    skipped = [task for task in order if task not in branches]
    approved = [task for task in order if task in branches]
    worktree = create_worktree(repo, run_id, "integrated", root, base_ref)
    merged: list[str] = []
    conflicts: list[IntegrationConflict] = []
    conflicted: list[str] = []
    try:
        for task in approved:
            proc = run_git(["merge", "--no-ff", "--no-edit", branches[task]], cwd=worktree, timeout=60)
            if proc.returncode == 0:
                merged.append(task)
                continue
            paths = _unmerged(worktree)
            text = proc.stdout + proc.stderr
            if not paths and "CONFLICT" not in text:
                raise GitError(text.strip() or "git merge failed")
            between = list(dict.fromkeys([*merged, task]))
            if len(between) < 2:
                other = approved[0] if approved[0] != task else approved[-1]
                between = [other, task]
            for path in paths or ["unknown"]:
                conflicts.append(IntegrationConflict(path=path, between=between))  # type: ignore[arg-type]
            aborted = run_git(["merge", "--abort"], cwd=worktree)
            if aborted.returncode != 0:
                raise GitError(aborted.stderr.strip() or "git merge --abort failed")
            conflicted.append(task)
        head = git_out(["rev-parse", "HEAD"], worktree).strip()
        check_results = _checks(worktree, checks) if checks else []
    finally:
        after = git_out(["rev-parse", "HEAD"], repo).strip()
        if after != main_head:
            raise GitError("integration touched the main checkout")
    excluded = [f"{task}: {why.get(task, 'not approved')}"[:300] for task in skipped]
    for task in conflicted:
        paths = [item.path for item in conflicts if task in item.between]
        excluded.append(f"{task}: conflict: {', '.join(paths) or 'merge'}"[:300])
    return IntegrationReport(
        branch=branch,
        head_sha=head,
        merged=merged,  # type: ignore[arg-type]
        conflicts=conflicts,
        skipped=skipped,  # type: ignore[arg-type]
        checks=check_results,
        excluded=excluded,
        merge_command=f"git -C {repo} merge {branch}",
        user_branch=user_branch,
        repo=str(repo),
    )
