"""Folder-first CLI. `treefleet "goal"` runs the pipeline from the current folder."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from treefleet.checks import detect_checks, python_prefix, select_checks, unavailable_reason
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
    from treefleet.pr.stage import status_lines, timings_for
    from treefleet.sessions import load_run

    if not argv or argv[0] not in {"watch", "status", "merge", "retry"}:
        print("usage: treefleet pr watch|status|merge|retry", file=sys.stderr)
        return 2
    settings = load_settings(Path.cwd() / "treefleet.toml")
    runs = Path.cwd() / "runs"
    if argv[0] in {"watch", "merge"} and not settings.pr.enabled:
        print("pr is disabled", file=sys.stderr)
        return 1
    if argv[0] == "merge":
        return _pr_merge(argv[1:], settings, runs)
    if argv[0] == "retry":
        return _pr_retry(argv[1:], runs)
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
                            "timings": timings_for(runs / argv[1] / "events.jsonl", task.task_id),
                        }
                        for task in record.tasks
                    ]
                )
            )
        )
        return 0
    return _pr_watch(record, settings)


def _pr_retry(argv: list[str], runs: Path) -> int:
    from treefleet.pr.retry import rearm_comment
    from treefleet.sessions import atomic_write, load_run

    if len(argv) != 2 or not argv[1].isdigit():
        print("usage: treefleet pr retry RUN PR", file=sys.stderr)
        return 2
    run_id = argv[0]
    path = runs / run_id / "run.json"
    if not path.is_file():
        print(f"no run {run_id}", file=sys.stderr)
        return 1
    events_path = runs / run_id / "events.jsonl"
    events: list[dict[str, object]] = []
    if events_path.is_file():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                loaded = json.loads(line)
                if isinstance(loaded, dict):
                    events.append(loaded)
    changed, message, updated = rearm_comment(load_run(path), events, int(argv[1]))
    print(message)
    if changed:
        atomic_write(path, updated.model_dump_json(indent=2) + "\n")
    return 0 if changed or message.startswith("comment ") else 1


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
    import asyncio
    import secrets

    from treefleet.contracts import Limits, RunEvent, TaskSpec, WorkerTaskEnvelope
    from treefleet.events import EventSink
    from treefleet.git.worktrees import kill_tree
    from treefleet.orchestrator.fleet import dispatch
    from treefleet.pr.accept import acceptance_prompt, parse_acceptance
    from treefleet.pr.gh import GhProvider
    from treefleet.pr.loop import CheckUnavailable, PollHooks, WorkerStartFailed
    from treefleet.pr.models import OpenPR, TrackedComment
    from treefleet.pr.stage import git_push, poll_until
    from treefleet.sessions import RunRecord, RunStore
    from treefleet.worker.spawner import before_spawn, crew_python, free_port, last_log_line, spawn_worker, worker_env

    if not isinstance(record, RunRecord):
        print("run record is unreadable", file=sys.stderr)
        return 1
    if record.stage not in {"awaiting_prs", "running"}:
        print(f"refusing watch: stage is {record.stage}", file=sys.stderr)
        return 1
    runs = Path.cwd() / "runs"
    path = runs / record.run_id / "run.json"
    store = RunStore(path, record)
    sink = EventSink(runs)
    sink.prime(record.run_id)
    plan_tasks = record.plan.get("tasks", []) if isinstance(record.plan, dict) else []
    specs = {str(item.get("id")): item for item in plan_tasks if isinstance(item, dict)}
    tasks = record.tasks
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
            comments=[
                TrackedComment(id=comment.id, body="", status=comment.status, rounds_used=comment.rounds_used)
                for comment in task.comments
            ],
        )
        for task in tasks
        if task.pr_number and task.pr_open
    ]

    def emit(kind: str, task_id: str, payload: dict[str, object]) -> None:
        print(f"{kind} {task_id}")
        sink.emit_sync(
            RunEvent(
                run_id=record.run_id,
                seq=0,
                source="orchestrator",
                type=kind,
                task_id=task_id or None,
                payload=payload,
            )
        )
        item = next((row for row in opened if row.task_id == task_id), None)
        if item is None:
            return
        store.upsert_task(
            task_id,
            pr_number=item.number,
            pr_url=item.url,
            pr_head_sha=item.head_sha,
            pr_last_comment_id=item.last_comment_id,
            pr_open=item.status not in {"merged", "cancelled"},
            pr_status=item.status,
            pr_checks=item.checks,
            comments=[
                {"id": comment.id, "status": comment.status, "rounds_used": comment.rounds_used}
                for comment in item.comments
            ],
            **(
                {"status": "needs_review", "reason": str(payload.get("reason") or "")}
                if kind == "pr.trust_unresolved"
                else {}
            ),
        )

    def revise(task_id: str, feedback: str) -> str | None:
        raw = specs.get(task_id)
        task_row = next((row for row in tasks if row.task_id == task_id), None)
        if raw is None or task_row is None or not task_row.worktree:
            print(f"no worktree for {task_id}", file=sys.stderr)
            return None
        worktree = Path(task_row.worktree)
        if not worktree.is_dir():
            print(f"worktree missing for {task_id}", file=sys.stderr)
            return None
        spec = TaskSpec.model_validate(raw)
        repo = Path(record.repo)
        selected = select_checks(
            repo,
            detect_checks(repo),
            worktree_root=Path(settings.worktree_root),  # type: ignore[attr-defined]
            run_id=record.run_id,
        )
        # The worker process is the CrewAI interpreter. select_checks above
        # already wrote the pytest interpreter into the check commands.
        python = crew_python(str(settings.worker_python))  # type: ignore[attr-defined]
        checks = {name: selected.checks[name] for name in spec.check_ids if name in selected.checks}
        extra = {"TREEFLEET_RUNS": str(runs), "TREEFLEET_CURSOR_CLI": settings.cursor.cli}  # type: ignore[attr-defined]
        prefix = python_prefix(checks)
        if prefix:
            extra["TREEFLEET_PYTHON_DIR"] = prefix
        envelope = WorkerTaskEnvelope(
            run_id=record.run_id,
            attempt=2,
            task=spec,
            model=settings.models.fast,  # type: ignore[attr-defined]
            cursor_model=_cursor_model(runs, record.run_id, task_id),
            checks=checks,
            limits=Limits(timeout_s=600),
            base_sha=record.base_sha or None,
            revision_feedback=feedback[:2000] or None,
        )
        token = secrets.token_urlsafe(32)
        port = free_port(settings.workers.port_start, set())  # type: ignore[attr-defined]
        log_path = runs / record.run_id / f"{task_id}-revision.log"
        try:
            before_spawn(worktree, record.run_id, sink, task_id)
            proc = spawn_worker(
                python=python,
                worktree=worktree,
                run_id=record.run_id,
                task_id=task_id,
                token=token,
                port=port,
                env=worker_env(token, extra),
                log_path=log_path,
            )
        except (OSError, RuntimeError, TimeoutError) as exc:
            reason = last_log_line(log_path) or type(exc).__name__
            print(f"worker did not start for {task_id}: {reason}", file=sys.stderr)
            raise WorkerStartFailed(reason) from exc
        try:
            result, _note = asyncio.run(dispatch(proc.url, token, envelope))
        except Exception as exc:
            print(f"revision failed for {task_id}: {type(exc).__name__}", file=sys.stderr)
            return None
        finally:
            kill_tree(proc.pid)
        missing = unavailable_reason(result.checks)
        if missing:
            raise CheckUnavailable(missing)
        if not result.commit_made or not result.head_sha:
            print(f"revision made no commit for {task_id}", file=sys.stderr)
            return None
        try:
            return git_push(Path(record.repo), record.run_id, task_id, settings.pr)  # type: ignore[attr-defined]
        except Exception as exc:
            print(f"push failed for {task_id}: {type(exc).__name__}", file=sys.stderr)
            return None

    def diff_of(task_id: str) -> str:
        import subprocess

        task_row = next((row for row in tasks if row.task_id == task_id), None)
        if task_row is None or not task_row.worktree:
            return ""
        proc = subprocess.run(
            ["git", "diff", "HEAD~1"],
            cwd=task_row.worktree,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        return proc.stdout

    def judge(task_id: str, comment: str, diff: str) -> tuple[str, str]:
        from google.adk.models.lite_llm import LiteLlm

        from treefleet.orchestrator.agents import _complete

        text = asyncio.run(
            _complete(
                LiteLlm(model=settings.models.fast),  # type: ignore[attr-defined]
                acceptance_prompt(comment, diff),
                instruction="Reply with one line: satisfied or unsatisfied, then a short reason.",
            )
        )
        return parse_acceptance(text)

    from treefleet.pr.trust import trusted_logins

    provider = GhProvider(settings.pr.repo)  # type: ignore[attr-defined]
    trusted, problem = trusted_logins(settings.pr, provider)  # type: ignore[attr-defined]
    if problem:
        for item in opened:
            item.status = "needs_review"
            emit("pr.trust_unresolved", item.task_id, {"reason": problem, "number": item.number})
        print(problem, file=sys.stderr)
        return 1

    outcome = poll_until(
        opened,
        provider,
        PollHooks(revise=revise, resolve_conflict=lambda _task: None, emit=emit, judge=judge, diff=diff_of),
        settings.pr,  # type: ignore[attr-defined]
        trusted=trusted,
    )
    for item in opened:
        store.upsert_task(
            item.task_id,
            pr_head_sha=item.head_sha,
            pr_last_comment_id=item.last_comment_id,
            pr_open=item.status not in {"merged", "cancelled"},
            pr_status=item.status,
            pr_checks=item.checks,
            comments=[
                {"id": comment.id, "status": comment.status, "rounds_used": comment.rounds_used}
                for comment in item.comments
            ],
        )
    print(outcome)
    return 0 if outcome == "done" else 1


def _cursor_model(runs: Path, run_id: str, task_id: str) -> str:
    path = runs / run_id / "events.jsonl"
    chosen = "composer-2.5"
    if not path.is_file():
        return chosen
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "model_selected" and event.get("task_id") == task_id:
                model = event.get("payload", {}).get("model")
                if isinstance(model, str) and model:
                    chosen = model
    return chosen


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
