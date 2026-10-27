"""Folder-first CLI. `treefleet "goal"` runs the pipeline from the current folder."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from treefleet.checks import detect_checks, select_checks
from treefleet.config import load_settings
from treefleet.contracts import RunRequest
from treefleet.cursor_models import UnknownModelError, discover, require_known, require_tiers
from treefleet.orchestrator.pipeline import ask_plan, cleanup_run, run_blocking


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "models":
        return _models()
    if args and args[0] == "cleanup":
        return _cleanup(args[1:])
    return _run(args)


def _confirmed_checks(
    repo: Path,
    no_checks: bool,
    yes: bool,
    extra: list[str],
    python: str | None,
    worktree_root: Path,
) -> tuple[dict[str, str], list[str]]:
    from treefleet.guards.artifacts import tracked_generated

    notes: list[str] = []
    generated = tracked_generated(repo)
    if generated:
        notes.append(("warning: repo tracks generated files: " + ", ".join(generated[:8]))[:300])
    checks = {} if no_checks else detect_checks(repo)
    for item in extra:
        name, sep, command = item.partition("=")
        if not sep or not name.strip() or not command.strip():
            raise SystemExit("check must be name=command")
        checks[name.strip()] = command.strip()
    if not checks:
        for note in notes:
            print(note)
        return {}, notes
    started = time.monotonic()
    selected = select_checks(
        repo,
        checks,
        worktree_root=worktree_root,
        explicit=Path(python) if python else None,
    )
    notes = [*notes, *selected.notes, f"baseline checks {time.monotonic() - started:.3f}s"]
    for note in notes:
        print(note)
    if selected.python_path:
        print(f"python: {selected.python_path} ({selected.python_source})")
    for name, command in selected.checks.items():
        print(f"{name}: {command}")
    if not selected.checks or yes or no_checks or extra:
        return selected.checks, notes
    answer = input("Use these checks? [Y/n] ")
    if answer.strip().lower() in {"n", "no"}:
        return {}, notes
    return selected.checks, notes


def _models() -> int:
    settings = load_settings(Path.cwd() / "treefleet.toml")
    for model in discover(Path(settings.cursor.cli), settings.cursor.list_flag):
        print(f"{model.id} - {model.label}")
    return 0


def _cleanup(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="treefleet cleanup")
    parser.add_argument("run_id")
    parser.add_argument("--repo", default=None)
    parsed = parser.parse_args(argv)
    repo = Path(parsed.repo or Path.cwd())
    for path in cleanup_run(repo, parsed.run_id):
        print(path)
    return 0


def _run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="treefleet")
    parser.add_argument("goal")
    parser.add_argument("--model", default="router")
    parser.add_argument("--repo", default=None)
    parser.add_argument("--no-approval", action="store_true")
    parser.add_argument("--yes", action="store_true", help="accept detected checks without asking")
    parser.add_argument("--no-checks", action="store_true", help="do not run detected checks")
    parser.add_argument("--check", action="append", default=[], help="name=command, repeatable")
    parser.add_argument("--python", default=None, help="interpreter for detected python checks")
    parser.add_argument(
        "--saboteur",
        default=None,
        metavar="TASK",
        help="DEMO ONLY: append an instruction to break an existing test on this task",
    )
    parsed = parser.parse_args(argv)
    worker_model = parsed.model
    settings = load_settings(Path.cwd() / "treefleet.toml")
    cli = Path(settings.cursor.cli)
    try:
        require_known(worker_model, cli, settings.cursor.list_flag)
        require_tiers(
            [
                settings.models.router.simple,
                settings.models.router.moderate,
                settings.models.router.complex,
                settings.models.default,
            ],
            cli,
            settings.cursor.list_flag,
        )
    except UnknownModelError as exc:
        raise SystemExit(str(exc)) from None
    repo = Path(parsed.repo or Path.cwd())
    checks, notes = _confirmed_checks(
        repo,
        parsed.no_checks,
        parsed.yes,
        parsed.check,
        parsed.python,
        Path(settings.worktree_root),
    )
    request = RunRequest(
        goal=parsed.goal,
        repo=str(repo),
        worker_model=worker_model,
        plan_approval=not parsed.no_approval,
        checks=checks,
        notes=notes,
        saboteur_task=parsed.saboteur,
        scenario="saboteur" if parsed.saboteur else "custom",
    )
    print(request.model_dump_json())
    outcome = run_blocking(request, settings, approve=ask_plan if request.plan_approval else None)
    if outcome.message and outcome.code != 0:
        print(outcome.message, file=sys.stderr)
    return outcome.code


if __name__ == "__main__":
    raise SystemExit(main())
