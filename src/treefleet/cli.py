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
from treefleet.orchestrator.pipeline import ask_plan, run_blocking


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "models":
        return _models()
    if args and args[0] == "reactions":
        return _reactions(args[1:])
    if args and args[0] == "sessions":
        return _sessions(args[1:])
    if args and args[0] == "cleanup":
        return _sessions(["cleanup", *args[1:]])
    if args and args[0] == "eval":
        return _eval(args[1:])
    if args and args[0] == "ui":
        return _ui(args[1:])
    if args and args[0] == "pr":
        return _pr(args[1:])
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
    parser.add_argument("--repo", default=None)
    parser.add_argument("--replay", default=None)
    parser.add_argument("--run", default=None)
    parser.add_argument("--port", type=int, default=8765)
    parsed = parser.parse_args(argv)
    from treefleet.evals.harness import load_env

    load_env(Path.cwd())
    replay = Path(parsed.replay) if parsed.replay else None
    repo = Path(parsed.repo) if parsed.repo else None
    if parsed.run:
        replay = Path.cwd() / "runs" / parsed.run / "events.jsonl"
        if not replay.is_file():
            print(f"no events at {replay}", file=sys.stderr)
            return 1
        if repo is None:
            record = Path.cwd() / "runs" / parsed.run / "run.json"
            if record.is_file():
                from treefleet.sessions import load_run

                repo = Path(load_run(record).repo)
    if repo is None:
        print("pass --repo or --run", file=sys.stderr)
        return 2
    if not (repo / ".git").exists():
        raise SystemExit("not a git repository")
    app = create_app(repo, replay=replay)
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


def _pr(argv: list[str]) -> int:
    """watch reattaches the poller. status prints one row. merge is the only merge command."""
    from treefleet.pr.stage import status_lines
    from treefleet.sessions import load_run

    if not argv or argv[0] not in {"watch", "status", "merge"}:
        print("usage: treefleet pr watch|status|merge", file=sys.stderr)
        return 2
    settings = load_settings(Path.cwd() / "treefleet.toml")
    runs = Path.cwd() / "runs"
    if argv[0] in {"watch", "merge"} and not settings.pr.enabled:
        print("pr is disabled", file=sys.stderr)
        return 1
    if argv[0] == "merge":
        return _pr_merge(argv[1:], settings, runs)
    if len(argv) != 2:
        print("usage: treefleet pr watch|status RUN", file=sys.stderr)
        return 2
    path = runs / argv[1] / "run.json"
    if not path.is_file():
        print(f"no run {argv[1]}", file=sys.stderr)
        return 1
    record = load_run(path)
    if argv[0] == "status":
        print(
            "\n".join(
                status_lines(
                    [
                        {
                            "task_id": task.task_id,
                            "pr_number": task.pr_number,
                            "pr_status": task.pr_status,
                            "status": task.status,
                            "pr_checks": task.pr_checks,
                        }
                        for task in record.tasks
                    ]
                )
            )
        )
        return 0
    return _pr_watch(record, settings)


def _pr_merge(argv: list[str], settings: object, runs: Path) -> int:
    from treefleet.pr.gh import GhProvider
    from treefleet.pr.stage import merge_task
    from treefleet.sessions import load_run

    if len(argv) != 1 or "/" not in argv[0]:
        print("usage: treefleet pr merge RUN/TASK", file=sys.stderr)
        return 2
    run_id, task_id = argv[0].split("/", 1)
    path = runs / run_id / "run.json"
    if not path.is_file():
        print(f"no run {run_id}", file=sys.stderr)
        return 1
    record = load_run(path)
    task = next((item for item in record.tasks if item.task_id == task_id), None)
    if task is None or not task.pr_number:
        print(f"no pull request for {task_id}", file=sys.stderr)
        return 1
    merge_task(GhProvider(settings.pr.repo), task.pr_number)  # type: ignore[attr-defined]
    print(f"merged {task.pr_number}")
    return 0


def _pr_watch(record: object, settings: object) -> int:
    from treefleet.pr.gh import GhProvider
    from treefleet.pr.loop import PollHooks
    from treefleet.pr.models import OpenPR
    from treefleet.pr.stage import poll_until

    tasks = record.tasks  # type: ignore[attr-defined]
    opened = [
        OpenPR(
            task_id=task.task_id,
            number=int(task.pr_number),
            url=task.pr_url,
            head=task.branch,
            head_sha=task.pr_head_sha,
            last_comment_id=task.pr_last_comment_id,
            status=task.pr_status or "open",
            checks=task.pr_checks or "pending",
        )
        for task in tasks
        if task.pr_number and task.pr_open
    ]

    def emit(kind: str, task_id: str, _payload: dict[str, object]) -> None:
        print(f"{kind} {task_id}")

    outcome = poll_until(
        opened,
        GhProvider(settings.pr.repo),  # type: ignore[attr-defined]
        PollHooks(revise=lambda _task, _feedback: None, resolve_conflict=lambda _task: None, emit=emit),
        settings.pr,  # type: ignore[attr-defined]
        login="",
    )
    print(outcome)
    return 0 if outcome == "done" else 1


def _sessions(argv: list[str]) -> int:
    from treefleet.sessions import cleanup_session, format_ls, format_show, kill_run, kill_task, list_runs, load_run

    parser = argparse.ArgumentParser(prog="treefleet sessions")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ls")
    show = sub.add_parser("show")
    show.add_argument("run_id")
    kill = sub.add_parser("kill")
    kill.add_argument("--task", default=None)
    kill.add_argument("--run", default=None)
    clean = sub.add_parser("cleanup")
    clean.add_argument("run_id")
    clean.add_argument("--yes", action="store_true")
    clean.add_argument("--force", action="store_true")
    clean.add_argument("--include-unmerged", action="store_true")
    clean.add_argument("--repo", default=None)
    parsed = parser.parse_args(argv)
    runs = Path.cwd() / "runs"
    if parsed.cmd == "ls":
        print(format_ls(list_runs(runs)))
        return 0
    if parsed.cmd == "show":
        path = runs / parsed.run_id / "run.json"
        if not path.is_file():
            print(f"no run {parsed.run_id}", file=sys.stderr)
            return 1
        print(format_show(load_run(path)))
        from treefleet.events import read_events, skip_message

        _rows, skipped = read_events(path.parent / "events.jsonl")
        if skipped:
            print(skip_message(skipped))
        return 0
    settings = load_settings(Path.cwd() / "treefleet.toml")
    root = Path(settings.worktree_root)
    if parsed.cmd == "kill":
        if bool(parsed.task) == bool(parsed.run):
            print("pass exactly one of --task RUN/TASK or --run RUN", file=sys.stderr)
            return 2
        if parsed.task:
            if "/" not in parsed.task:
                print("--task needs RUN/TASK", file=sys.stderr)
                return 2
            run_id, task_id = parsed.task.split("/", 1)
            result = kill_task(runs, run_id, task_id, root)
        else:
            result = kill_run(runs, parsed.run, root)
        print("\n".join(result.lines))
        return result.code
    supplied = Path(parsed.repo) if parsed.repo else None
    result = cleanup_session(
        parsed.run_id,
        repo=supplied or Path.cwd(),
        runs=runs,
        worktree_root=root,
        yes=parsed.yes,
        force=parsed.force,
        include_unmerged=parsed.include_unmerged,
        supplied=supplied,
    )
    print("\n".join(result.lines))
    return result.code


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
