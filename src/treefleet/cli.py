"""Folder-first CLI. `treefleet "goal"` runs the pipeline from the current folder."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from treefleet.checks import detect_checks
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


def _confirmed_checks(repo: Path, no_checks: bool, yes: bool, extra: list[str]) -> dict[str, str]:
    checks = {} if no_checks else detect_checks(repo)
    for item in extra:
        name, sep, command = item.partition("=")
        if not sep or not name.strip() or not command.strip():
            raise SystemExit("check must be name=command")
        checks[name.strip()] = command.strip()
    for name, command in checks.items():
        print(f"{name}: {command}")
    if not checks or yes or no_checks or extra:
        return checks
    answer = input("Use these checks? [Y/n] ")
    if answer.strip().lower() in {"n", "no"}:
        return {}
    return checks


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
    checks = _confirmed_checks(repo, parsed.no_checks, parsed.yes, parsed.check)
    request = RunRequest(
        goal=parsed.goal,
        repo=str(repo),
        worker_model=worker_model,
        plan_approval=not parsed.no_approval,
        checks=checks,
    )
    print(request.model_dump_json())
    outcome = run_blocking(request, settings, approve=ask_plan if request.plan_approval else None)
    if outcome.message and outcome.code != 0:
        print(outcome.message, file=sys.stderr)
    return outcome.code


if __name__ == "__main__":
    raise SystemExit(main())
