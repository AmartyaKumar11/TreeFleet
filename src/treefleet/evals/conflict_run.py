"""Live deterministic conflict proof: seeded branches + real integrate + Cursor resolve."""

from __future__ import annotations

import secrets
import time
from pathlib import Path
from typing import Any

from treefleet.checks import detect_checks, select_checks
from treefleet.config import Settings
from treefleet.contracts import FinalReport, IsolationReport, assemble_metrics
from treefleet.cursor_run import run_cursor
from treefleet.evals.conflict_seed import seed_conflicting_tasks
from treefleet.events import EventSink
from treefleet.git.canary import write_canary
from treefleet.git.merge import conflict_revision_feedback, integrate, merge_tip_into_task
from treefleet.git.proofs import snapshot_main, verify_main, verify_task_isolation
from treefleet.git.worktrees import git_out, run_git
from treefleet.orchestrator.pipeline import cleanup_run


def _commit_resolve(worktree: Path, message: str = "tf resolve conflict") -> str | None:
    status = run_git(["status", "--porcelain"], cwd=worktree)
    if not (status.stdout or "").strip():
        # Merge may have completed cleanly after Cursor edited and staged.
        unmerged = run_git(["diff", "--name-only", "--diff-filter=U"], cwd=worktree)
        if (unmerged.stdout or "").strip():
            return None
        return git_out(["rev-parse", "HEAD"], worktree).strip()
    run_git(["add", "-A"], cwd=worktree)
    proc = run_git(["commit", "-m", message], cwd=worktree)
    if proc.returncode != 0:
        return None
    return git_out(["rev-parse", "HEAD"], worktree).strip()


def cursor_resolve_conflict(
    *,
    worktree: Path,
    tip: str,
    paths: list[str],
    settings: Settings,
    sink: EventSink | None,
    run_id: str,
    task_id: str,
    allowed: list[str],
) -> bool:
    """Merge integrated tip into the task worktree and ask Cursor to clear conflict markers."""
    merge_tip_into_task(worktree, tip)
    feedback = conflict_revision_feedback(paths, tip)
    run_cursor(
        cli=Path(settings.cursor.cli),
        worktree=worktree,
        instruction=feedback,
        allowed=allowed,
        denied=[],
        model=settings.models.default or None,
        timeout_s=300,
        sink=sink,
        run_id=run_id,
        task_id=task_id,
    )
    for path in paths:
        target = worktree / path
        if not target.is_file():
            return False
        text = target.read_text(encoding="utf-8")
        if "<<<<<<<" in text or ">>>>>>>" in text:
            return False
    return _commit_resolve(worktree) is not None


def run_deterministic_conflict(
    root: Path,
    repo: Path,
    settings: Settings,
    *,
    runs_root: Path | None = None,
    worktree_root: Path | None = None,
) -> dict[str, Any]:
    """Seed alpha/beta conflicts, integrate with one Cursor resolve, score like an eval row."""
    from treefleet.evals.harness import porcelain, score
    from treefleet.evals.scenarios import Expectation, Scenario

    run_id = secrets.token_hex(4)
    runs = runs_root or root / "runs"
    run_dir = runs / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    sink = EventSink(run_dir / "events.jsonl")
    wt_root = worktree_root or Path(settings.worktree_root)
    write_canary(repo, run_id)
    before = snapshot_main(repo)
    base = before.head
    readme = (repo / "README.md").read_text(encoding="utf-8") if (repo / "README.md").is_file() else "# Demo\n\nline\n"
    lines = readme.splitlines() or ["# Demo", "", "Small Python app."]
    body_idx = next((i for i, line in enumerate(lines) if i > 0 and line.strip()), 1 if len(lines) > 1 else 0)
    alpha_lines = list(lines)
    beta_lines = list(lines)
    while len(alpha_lines) <= body_idx:
        alpha_lines.append("")
        beta_lines.append("")
    alpha_lines[body_idx] = "Alpha owns this sentence about the PR Sentinel demo app."
    beta_lines[body_idx] = "Beta owns this sentence about the PR Sentinel demo app."
    seeded = seed_conflicting_tasks(
        repo,
        run_id=run_id,
        worktree_root=wt_root,
        path="README.md",
        alpha_text="\n".join(alpha_lines) + "\n",
        beta_text="\n".join(beta_lines) + "\n",
        base_ref=base,
    )
    detected = detect_checks(repo)
    selected = select_checks(repo, detected, worktree_root=wt_root)
    started = time.monotonic()
    resolve_calls: list[str] = []

    def resolve_once(task_id: str, tip: str, paths: list[str]) -> bool:
        resolve_calls.append(task_id)
        return cursor_resolve_conflict(
            worktree=seeded.worktrees[task_id],
            tip=tip,
            paths=paths,
            settings=settings,
            sink=sink,
            run_id=run_id,
            task_id=task_id,
            allowed=["README.md"],
        )

    integration = integrate(
        repo,
        run_id,
        ["alpha", "beta"],
        seeded.branches,
        base,
        worktree_root=wt_root,
        checks=dict(selected.checks) or None,
        resolve_once=resolve_once,
    )
    integrate_s = time.monotonic() - started
    proofs = [
        verify_task_isolation(
            repo,
            task_id=task_id,
            worktree=seeded.worktrees[task_id],
            run_id=run_id,
            base_ref=base,
            allowed_paths=list(task.allowed_paths),
            denied_paths=list(task.denied_paths),
            pid=None,
        )
        for task_id, task in ((item.id, item) for item in seeded.plan.tasks)
    ]
    isolation = IsolationReport(tasks=proofs, main=verify_main(repo, before))
    wall = time.monotonic() - started
    metrics = assemble_metrics(
        wall=wall,
        pre_fleet=0.0,
        fleet_wall=0.0,
        task_seconds=0.0,
        task_count=2,
        review_seconds=0.0,
        integration_seconds=integrate_s,
    )
    report = FinalReport(
        run_id=run_id,  # type: ignore[arg-type]
        goal=seeded.plan.goal,
        plan=seeded.plan,
        results=list(seeded.results.values()),
        verdicts=list(seeded.verdicts.values()),
        isolation=isolation,
        integration=integration,
        metrics=metrics,
        notes=[
            f"deterministic conflict resolve calls: {', '.join(resolve_calls) or 'none'}"[:300],
            f"integrate {integrate_s:.1f}s"[:300],
        ],
    )
    (run_dir / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "report.md").write_text(report.to_markdown(), encoding="utf-8")
    scenario = Scenario(
        id="E7",
        goal=seeded.plan.goal,
        mode="deterministic_conflict",
        expect=Expectation(
            guard="allow",
            task_min=2,
            task_max=2,
            statuses=["done"],
            verdicts=["approve"],
            isolation="PASS",
            checks="pass",
        ),
    )
    row = score(scenario, report, report.to_markdown())
    row["label"] = "E7#1"
    row["run_id"] = run_id
    row["mode"] = "deterministic_conflict"
    row["resolve_calls"] = list(resolve_calls)
    row["merged"] = list(integration.merged)
    row["conflict_paths"] = [item.path for item in integration.conflicts]
    if not resolve_calls:
        row["expectation"] = "fail: no conflict resolve attempted"
    elif list(integration.merged) != ["alpha", "beta"]:
        row["expectation"] = f"fail: merged {list(integration.merged)}"[:200]
    row["checkout"] = porcelain(repo)
    cleanup_run(repo, run_id)
    row["checkout"] = porcelain(repo)
    return row
