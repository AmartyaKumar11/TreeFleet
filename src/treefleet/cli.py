"""Folder-first CLI. `treefleet "goal"` runs the pipeline from the current folder."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from treefleet.checks import detect_checks, select_checks
from treefleet.config import load_settings
from treefleet.contracts import RunRequest
from treefleet.cursor_models import UnknownModelError, discover, require_known, require_tiers  # noqa: F401
from treefleet.orchestrator.pipeline import ask_plan, cleanup_run, run_blocking


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "models":
        return _models()
    if args and args[0] == "reactions":
        return _reactions(args[1:])
    if args and args[0] == "cleanup":
        return _cleanup(args[1:])
    if args and args[0] == "eval":
        return _eval(args[1:])
    if args and args[0] == "ui":
        return _ui(args[1:])
    if args and args[0] == "demo":
        from treefleet.demo import main as demo_main

        return demo_main(args[1:])
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


def _eval(argv: list[str]) -> int:
    from treefleet.evals.harness import harness_status, load_env, run_live
    from treefleet.evals.report import enrich, merge_rows, render_json, render_markdown
    from treefleet.evals.scenarios import load_scenarios

    parser = argparse.ArgumentParser(prog="treefleet eval")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--scenarios", default="eval/scenarios.toml")
    parser.add_argument("--only", default="", help="comma-separated scenario ids")
    parsed = parser.parse_args(argv)
    root = Path.cwd()
    load_env(root)
    settings = load_settings(root / "treefleet.toml")
    cap = settings.workers.max_workers
    folder = root / "eval"
    folder.mkdir(parents=True, exist_ok=True)
    evidence = root / "evidence" / "phase-9"
    evidence.mkdir(parents=True, exist_ok=True)

    previous: list[dict[str, object]] = []
    existing = folder / "report.json"
    if parsed.only and existing.is_file():
        loaded = json.loads(existing.read_text(encoding="utf-8"))
        previous = list(loaded.get("scenarios") or [])

    def _save(rows: list[dict[str, object]]) -> None:
        merged = merge_rows(previous, rows) if previous else rows
        enrich(merged, root / "runs")
        markdown = render_markdown(merged, cap)
        body = render_json(merged, cap)
        (folder / "report.md").write_text(markdown, encoding="utf-8")
        (folder / "report.json").write_text(body, encoding="utf-8")
        (evidence / "eval-report.md").write_text(markdown, encoding="utf-8")
        (evidence / "eval-report.json").write_text(body, encoding="utf-8")

    chosen = load_scenarios(root / parsed.scenarios)
    if parsed.only:
        wanted = {item.strip() for item in parsed.only.split(",") if item.strip()}
        chosen = [item for item in chosen if item.id in wanted]
    rows = run_live(root, Path(parsed.repo), chosen, on_row=_save)
    print((folder / "report.md").read_text(encoding="utf-8"))
    if any(row.get("checkout") for row in rows):
        return 1
    return harness_status(rows)


def _ui(argv: list[str]) -> int:
    import uvicorn

    from treefleet.ui.server import create_app

    parser = argparse.ArgumentParser(prog="treefleet ui")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--replay", default=None)
    parser.add_argument("--port", type=int, default=8765)
    parsed = parser.parse_args(argv)
    from treefleet.evals.harness import load_env

    load_env(Path.cwd())
    repo = Path(parsed.repo)
    if not (repo / ".git").exists():
        raise SystemExit("not a git repository")
    app = create_app(repo, replay=Path(parsed.replay) if parsed.replay else None)
    uvicorn.run(app, host="127.0.0.1", port=parsed.port, log_level="warning")
    return 0


def _reactions(argv: list[str]) -> int:
    from treefleet.reactions import explain_events, format_list

    parser = argparse.ArgumentParser(prog="treefleet reactions")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    explain = sub.add_parser("explain")
    explain.add_argument("--run", required=True)
    parsed = parser.parse_args(argv)
    if parsed.cmd == "list":
        settings = load_settings(Path.cwd() / "treefleet.toml")
        print(format_list(settings.reactions))
        return 0
    path = Path.cwd() / "runs" / parsed.run / "events.jsonl"
    if not path.is_file():
        print(f"no events at {path}", file=sys.stderr)
        return 1
    print(explain_events(path.read_text(encoding="utf-8").splitlines()))
    return 0


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
    parser.add_argument(
        "--include-needs-review",
        action="store_true",
        help="merge branches whose review is unresolved but whose diff and checks are valid",
    )
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
        # Unknown tier ids fall back at routing time. They do not abort the run.
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
        include_needs_review=parsed.include_needs_review,
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
