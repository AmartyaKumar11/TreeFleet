"""Merge approved task branches in plan order. Conflicts are recorded and the merge is aborted."""

from __future__ import annotations

from pathlib import Path

from treefleet.contracts import IntegrationConflict, IntegrationReport
from treefleet.git.worktrees import GitError, git_out, run_git


def _unmerged(repo: Path) -> list[str]:
    proc = run_git(["-c", "core.quotepath=false", "diff", "--name-only", "--diff-filter=U"], cwd=repo)
    if proc.returncode != 0:
        return []
    return [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]


def integrate(
    repo: Path,
    run_id: str,
    order: list[str],
    branches: dict[str, str],
    base_ref: str,
) -> IntegrationReport:
    branch = f"tf/{run_id}/integration"
    skipped = [task for task in order if task not in branches]
    approved = [task for task in order if task in branches]
    created = run_git(["checkout", "-b", branch, base_ref], cwd=repo)
    if created.returncode != 0:
        raise GitError(created.stderr.strip() or "could not create the integration branch")
    merged: list[str] = []
    conflicts: list[IntegrationConflict] = []
    for index, task in enumerate(approved):
        proc = run_git(["merge", "--no-ff", "--no-edit", branches[task]], cwd=repo, timeout=60)
        if proc.returncode == 0:
            merged.append(task)
            continue
        paths = _unmerged(repo)
        text = proc.stdout + proc.stderr
        if not paths and "CONFLICT" not in text:
            raise GitError(text.strip() or "git merge failed")
        between = [*merged, task]
        if len(between) < 2:
            between = [task, approved[0] if approved[0] != task else approved[-1]]
        for path in paths or ["unknown"]:
            conflicts.append(IntegrationConflict(path=path, between=between))
        aborted = run_git(["merge", "--abort"], cwd=repo)
        if aborted.returncode != 0:
            raise GitError(aborted.stderr.strip() or "git merge --abort failed")
        skipped.extend(approved[index + 1 :])
        break
    head = git_out(["rev-parse", "HEAD"], repo).strip()
    return IntegrationReport(branch=branch, head_sha=head, merged=merged, conflicts=conflicts, skipped=skipped)
