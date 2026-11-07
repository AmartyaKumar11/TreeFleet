"""Seed two approved task branches that conflict on the same lines. Plain git only."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path

from treefleet.contracts import (
    FileChange,
    Plan,
    ReviewVerdict,
    SelfReportedIsolation,
    TaskSpec,
    WorkerResult,
)
from treefleet.git.worktrees import branch_name, create_worktree, git_out, run_git


@dataclass
class SeededConflict:
    run_id: str
    plan: Plan
    branches: dict[str, str]
    worktrees: dict[str, Path]
    results: dict[str, WorkerResult]
    verdicts: dict[str, ReviewVerdict]


def _commit_file(worktree: Path, relative: str, text: str, message: str) -> str:
    target = worktree / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    proc = run_git(["add", "--", relative], cwd=worktree)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git add failed")
    proc = run_git(["commit", "-m", message], cwd=worktree)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git commit failed")
    return git_out(["rev-parse", "HEAD"], worktree).strip()


def seed_conflicting_tasks(
    repo: Path,
    *,
    run_id: str | None = None,
    worktree_root: Path,
    path: str = "README.md",
    alpha_text: str,
    beta_text: str,
    base_ref: str = "HEAD",
) -> SeededConflict:
    """Create alpha/beta worktrees that both change `path` with different content."""
    rid = run_id or secrets.token_hex(4)
    plan = Plan(
        goal="Deterministic same-hunk conflict for integration feedback.",
        rationale="Harness-seeded conflict. Workers did not create these branches.",
        tasks=[
            TaskSpec(
                id="alpha",  # type: ignore[arg-type]
                title="Alpha same-hunk edit",
                instructions="Harness seeded Alpha's edit of the shared file lines.",
                allowed_paths=[path],  # type: ignore[list-item]
            ),
            TaskSpec(
                id="beta",  # type: ignore[arg-type]
                title="Beta same-hunk edit",
                instructions="Harness seeded Beta's edit of the shared file lines.",
                allowed_paths=[path],  # type: ignore[list-item]
            ),
        ],
    )
    worktrees: dict[str, Path] = {}
    branches: dict[str, str] = {}
    results: dict[str, WorkerResult] = {}
    verdicts: dict[str, ReviewVerdict] = {}
    texts = {"alpha": alpha_text, "beta": beta_text}
    for task_id, body in texts.items():
        wt = create_worktree(repo, rid, task_id, worktree_root, base_ref)
        sha = _commit_file(wt, path, body, f"tf {task_id}")
        branch = branch_name(rid, task_id)
        worktrees[task_id] = wt
        branches[task_id] = branch
        results[task_id] = WorkerResult(
            run_id=rid,  # type: ignore[arg-type]
            task_id=task_id,  # type: ignore[arg-type]
            attempt=1,
            status="done",
            branch=branch,  # type: ignore[arg-type]
            head_sha=sha,  # type: ignore[arg-type]
            commit_made=True,
            model="harness",
            files_changed=[FileChange(path=path, additions=1, deletions=1)],  # type: ignore[arg-type]
            isolation=SelfReportedIsolation(
                pid=1,
                cwd=str(wt),
                toplevel=str(wt),
                branch=branch,
                mcp_root=str(wt),
            ),
        )
        verdicts[task_id] = ReviewVerdict(
            task_id=task_id,  # type: ignore[arg-type]
            verdict="approve",
            confidence=1,
            reasons=["harness seeded"],
        )
    return SeededConflict(
        run_id=rid,
        plan=plan,
        branches=branches,
        worktrees=worktrees,
        results=results,
        verdicts=verdicts,
    )
