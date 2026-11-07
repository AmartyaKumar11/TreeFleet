"""Merge approved task branches in plan order. Conflicts are recorded and the merge is aborted."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from treefleet.checks import run_command
from treefleet.contracts import CheckResult, IntegrationConflict, IntegrationReport
from treefleet.git.worktrees import GitError, branch_name, create_worktree, git_out, run_git
from treefleet.reactions import Action, Decision, Event, ReactionTable, resolve

_CONFLICT_FEEDBACK_CAP = 1800


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


def conflict_revision_feedback(paths: list[str], tip: str) -> str:
    """Ask the owning worker to resolve merge markers left by merging the integrated tip."""
    listed = ", ".join(paths) if paths else "unknown"
    short = tip[:12] if tip else "HEAD"
    text = (
        "Merge conflict with the integrated branch tip "
        f"{short}. Resolve every conflict marker in: {listed}. "
        "Keep both tasks' intent where possible, then leave a clean committed tree. "
        "Do not leave <<<<<<< markers."
    )
    return text[:_CONFLICT_FEEDBACK_CAP]


def merge_tip_into_task(worktree: Path, tip: str) -> list[str]:
    """Merge tip into the task worktree. Returns remaining unmerged paths (empty if clean)."""
    proc = run_git(["merge", "--no-edit", tip], cwd=worktree, timeout=60)
    if proc.returncode == 0:
        return []
    paths = _unmerged(worktree)
    text = proc.stdout + proc.stderr
    if not paths and "CONFLICT" not in text:
        raise GitError(text.strip() or "git merge tip into task failed")
    return paths or ["unknown"]


def _abort_merge(worktree: Path) -> None:
    aborted = run_git(["merge", "--abort"], cwd=worktree)
    if aborted.returncode != 0:
        raise GitError(aborted.stderr.strip() or "git merge --abort failed")


def _try_merge(worktree: Path, branch: str) -> tuple[bool, list[str], str]:
    proc = run_git(["merge", "--no-ff", "--no-edit", branch], cwd=worktree, timeout=60)
    if proc.returncode == 0:
        return True, [], ""
    paths = _unmerged(worktree)
    text = proc.stdout + proc.stderr
    return False, paths, text


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
    resolve_once: Callable[[str, str, list[str]], bool] | None = None,
    reactions: ReactionTable | None = None,
    on_reaction: Callable[[str, Decision], None] | None = None,
) -> IntegrationReport:
    """Merge into tf/<run>/integrated in its own worktree. The caller's checkout is not moved.

    resolve_once(task_id, integrated_tip, conflict_paths) may merge the tip into the task
    branch, have Cursor resolve, and return True to retry this task once. A second conflict
    excludes the task. Never auto-resolves with an LLM outside Cursor.
    """
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
    conflict_tries: dict[str, int] = {}
    try:
        for task in approved:
            ok, paths, text = _try_merge(worktree, branches[task])
            if ok:
                merged.append(task)
                continue
            if not paths and "CONFLICT" not in text:
                raise GitError(text.strip() or "git merge failed")
            between = list(dict.fromkeys([*merged, task]))
            if len(between) < 2:
                other = approved[0] if approved[0] != task else approved[-1]
                between = [other, task]
            conflict_paths = paths or ["unknown"]
            _abort_merge(worktree)
            tip = git_out(["rev-parse", "HEAD"], worktree).strip()
            while resolve_once is not None:
                attempt = conflict_tries.get(task, 0) + 1
                decision = resolve(Event.merge_conflict, attempt, reactions)
                if on_reaction is not None:
                    on_reaction(task, decision)
                if decision.action != Action.send_to_agent:
                    break
                conflict_tries[task] = attempt
                if not resolve_once(task, tip, conflict_paths):
                    break
                ok, paths, text = _try_merge(worktree, branches[task])
                if ok:
                    merged.append(task)
                    break
                if not paths and "CONFLICT" not in text:
                    raise GitError(text.strip() or "git merge failed after resolve")
                conflict_paths = paths or ["unknown"]
                between = list(dict.fromkeys([*merged, task]))
                if len(between) < 2:
                    other = approved[0] if approved[0] != task else approved[-1]
                    between = [other, task]
                _abort_merge(worktree)
                tip = git_out(["rev-parse", "HEAD"], worktree).strip()
            if task in merged:
                continue
            for path in conflict_paths:
                conflicts.append(IntegrationConflict(path=path, between=between))  # type: ignore[arg-type]
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
