"""treefleet try: local sample first run. No GitHub, no PR mode."""

from __future__ import annotations

import argparse
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from treefleet.config import Settings, load_settings
from treefleet.confirm import confirm_run
from treefleet.contracts import RunRequest
from treefleet.orchestrator.pipeline import ask_plan, run_blocking
from treefleet.pr.config import default_pr
from treefleet.sample.create import DEFAULT_GOAL, default_sample_dir, ensure_sample

DoctorFn = Callable[..., tuple[int, list[tuple[bool, str]]]]
PipelineFn = Callable[..., Any]
ConfirmFn = Callable[..., tuple[int, list[str]]]


def _head(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        shell=False,
        check=False,
    )
    return proc.stdout.strip()


def _tf_branches(repo: Path, run_id: str) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo), "for-each-ref", "--format=%(refname:short)", "refs/heads/"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        shell=False,
        check=False,
    )
    prefix = f"tf/{run_id}/"
    return [line.strip() for line in proc.stdout.splitlines() if line.strip().startswith(prefix)]


def _summary(repo: Path, settings: Settings, run_id: str, outcome: Any) -> list[str]:
    runs = Path.cwd() / "runs" / run_id
    report = runs / "report.md"
    cost = ""
    if outcome.report is not None:
        total = getattr(outcome.report.metrics, "total_usd", None) if outcome.report.metrics else None
        if total is not None:
            cost = f"cost: ${total:.4f}"
    branches = _tf_branches(repo, run_id)
    lines = [
        "=== try results ===",
        f"run_id: {run_id}",
        f"sample: {repo}",
        f"worktree root: {settings.worktree_root}",
        f"main HEAD (unchanged by auto-merge): {_head(repo)}",
        f"task branches: {', '.join(branches) if branches else '(none yet)'}",
        f"report: {report}",
    ]
    if cost:
        lines.append(cost)
    lines.extend(
        [
            "inspect (not run for you):",
            f"  git -C {repo} log --oneline --decorate -n 12",
            f"  git -C {repo} branch --list 'tf/{run_id}/*'",
            f"  git -C {repo} diff main...tf/{run_id}/<task>",
            f"  git -C {repo} merge --no-ff tf/{run_id}/<task>   # only if you choose",
            "TreeFleet did not merge into main.",
        ]
    )
    return lines


def run_try(
    *,
    dest: Path | None = None,
    yes: bool = False,
    goal: str | None = None,
    settings: Settings | None = None,
    doctor: DoctorFn | None = None,
    confirm: ConfirmFn | None = None,
    pipeline: PipelineFn | None = None,
    ask: Callable[[str], str] | None = None,
    interactive: bool | None = None,
) -> tuple[int, list[str]]:
    """Create/reuse sample, doctor, confirm, run with PR forced off. Returns exit code + notes."""
    notes: list[str] = []
    sample = dest or default_sample_dir()
    code, created = ensure_sample(sample)
    notes.extend(created)
    for line in created:
        print(line, flush=True)
    if code != 0:
        return code, notes

    loaded = settings or load_settings()
    # Force PR off for this run only.
    run_settings = loaded.model_copy(update={"pr": default_pr()})

    doctor_fn = doctor
    if doctor_fn is None:
        from treefleet.onboarding import run_doctor

        doctor_fn = run_doctor

    # run_doctor prints its own rows; keep them in notes for callers/tests.
    doc_code, doc_rows = doctor_fn(repo=sample, live=False, want_pr=False)
    for _ok, text in doc_rows:
        notes.append(text)
    if doc_code != 0:
        msg = "doctor red; not starting a run"
        notes.append(msg)
        print(msg, flush=True)
        return 1, notes

    confirm_fn = confirm or confirm_run
    conf_code, conf_lines = confirm_fn(
        sample,
        run_settings,
        yes=yes,
        ask=ask,
        interactive=interactive,
    )
    notes.extend(conf_lines)
    for line in conf_lines:
        print(line, flush=True)
    if conf_code != 0:
        return conf_code, notes

    # --yes skips prompts only. Plan still prints and auto-approves so the stranger sees it.
    def _auto_plan(plan: Any) -> bool:
        print(plan.model_dump_json(), flush=True)
        print("Approve plan? [y/N] y  (auto --yes)", flush=True)
        return True

    request = RunRequest(
        goal=goal or DEFAULT_GOAL,
        repo=str(sample),
        worker_model="router",
        plan_approval=True,
        checks={},
        notes=[],
        scenario="custom",
    )
    pipe = pipeline or (lambda req, sett, **kwargs: run_blocking(req, sett, approve=kwargs.get("approve")))
    outcome = pipe(
        request,
        run_settings,
        approve=_auto_plan if yes else ask_plan,
    )
    run_id = getattr(outcome, "run_id", "") or ""
    if getattr(outcome, "code", 1) != 0:
        msg = getattr(outcome, "message", "") or "run failed"
        notes.append(msg)
        print(msg, flush=True)
        if run_id:
            summary = _summary(sample, run_settings, run_id, outcome)
            notes.extend(summary)
            for line in summary:
                print(line, flush=True)
        return 1, notes
    summary = _summary(sample, run_settings, run_id, outcome)
    notes.extend(summary)
    for line in summary:
        print(line, flush=True)
    # Safety: sample text must never mention the demo remote.
    blob = "\n".join(notes)
    if "pr-sentinel-demo" in blob or "AmartyaKumar11" in blob:
        msg = "internal error: demo remote leaked into try output"
        notes.append(msg)
        print(msg, flush=True)
        return 1, notes
    return 0, notes


def try_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="treefleet try",
        description="Create a local sample repo and run TreeFleet once (no GitHub, no PR mode).",
    )
    parser.add_argument(
        "--dir",
        default=None,
        help=f"sample path (default: {default_sample_dir()})",
    )
    parser.add_argument("--yes", action="store_true", help="skip confirmation prompts")
    parser.add_argument("--goal", default=None, help="override the default three-task goal")
    try:
        parsed = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        code = exc.code
        return 2 if code else 0
    from treefleet.evals.harness import load_env

    load_env(Path.cwd())
    code, _notes = run_try(
        dest=Path(parsed.dir) if parsed.dir else None,
        yes=parsed.yes,
        goal=parsed.goal,
    )
    return code
