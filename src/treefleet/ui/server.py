"""Localhost board. The pipeline runs in a background thread. Replay never starts one."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from treefleet.config import Settings, load_settings
from treefleet.contracts import Plan, RunRequest
from treefleet.git.worktrees import run_git
from treefleet.guards.input import deterministic_reasons, screen_goal
from treefleet.guards.text import redact_payload
from treefleet.ui.lanes import run_needs_human, tasks_from_snapshot

_PAGE = Path(__file__).with_name("board.html")
_STATIC = Path(__file__).with_name("static")
_STAGES = ("guard", "plan", "approve", "fleet", "review", "integrate", "done")


def _stub_fakes() -> Any:
    """Offline fleet for the capture script. The live UI leaves this unset."""
    if os.environ.get("TREEFLEET_STUB_FLEET") != "1":
        return None
    from treefleet.orchestrator.agents import scripted_llm
    from treefleet.orchestrator.pipeline import OfflineFakes

    # The shim writes README.md and marker.txt. Those paths keep the edit in scope.
    plan = json.dumps(
        {
            "goal": "Add unit tests for src/notifications.py and, as a second task, add unit tests for src/admin.py.",
            "rationale": "Two isolated edits so the board can show the fleet.",
            "tasks": [
                {
                    "id": "test-notifications",
                    "title": "Notification tests",
                    "instructions": "Add unit tests for src/notifications.py.",
                    "allowed_paths": ["README.md", "marker.txt"],
                },
                {
                    "id": "test-admin",
                    "title": "Admin tests",
                    "instructions": "Add unit tests for src/admin.py.",
                    "allowed_paths": ["README.md", "marker.txt"],
                },
            ],
        }
    )
    return OfflineFakes(
        planner=scripted_llm(plan),
        cursor_cli=Path(__file__).resolve().parents[3] / "spikes" / "s9_fakes.py",
        mock_llm="1",
        fake_edit="readme",
        fake_sleep=os.environ.get("TREEFLEET_FAKE_SLEEP", "2"),
        skip_jev=True,
    )


def replay_gap(previous: str | None, stamp: str, cap: float = 30) -> float:
    """Seconds to wait between two recorded events. A gap above the cap plays at the cap."""
    if not previous or not stamp:
        return 0
    from datetime import datetime

    try:
        gap = (datetime.fromisoformat(stamp) - datetime.fromisoformat(previous)).total_seconds()
    except ValueError:
        return 0
    if gap <= 0:
        return 0
    return min(gap, cap)


def local_host(value: str) -> bool:
    name = value.split(",")[0].strip().split(":")[0].strip("[]")
    return name in {"127.0.0.1", "localhost"}


class Approval:
    """Blocks the planner until the page approves or cancels."""

    def __init__(self) -> None:
        self.plan: Plan | None = None
        self._decision = threading.Event()
        self.ok = False
        self.source = "http"

    def __call__(self, plan: Plan) -> bool:
        self.plan = plan
        self._decision.wait()
        return self.ok

    def waiting(self) -> bool:
        return self.plan is not None and not self._decision.is_set()

    def finish(self, ok: bool, source: str = "http") -> None:
        self.ok = ok
        self.source = "ui" if source == "ui" else "http"
        self._decision.set()


def rows_for_run(rows: list[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    """Keep worktrees whose branch is exactly tf/<run_id>/..."""
    prefix = f"tf/{run_id}/"
    return [row for row in rows if str(row.get("branch") or "").startswith(prefix)]


def worktree_rows(repo: Path, base_sha: str = "HEAD") -> list[dict[str, Any]]:
    """Worktrees other than the main checkout.

    ``files`` is the working tree against base_sha (the diff drawer). ``committed`` is
    ``git diff --numstat base...HEAD`` and is what a card's line count uses.
    """
    from treefleet.git.proofs import committed_numstat, diff_stats
    from treefleet.git.worktrees import list_worktrees

    rows: list[dict[str, Any]] = []
    try:
        found = list_worktrees(repo)
    except Exception:
        return rows
    main = repo.resolve()
    for item in found:
        try:
            if item.path.resolve() == main:
                continue
        except OSError:
            continue
        base = base_sha or "HEAD"
        try:
            files = [change.model_dump() for change in diff_stats(item.path, base)]
        except Exception:
            files = []
        try:
            committed = [change.model_dump() for change in committed_numstat(item.path, base)]
        except Exception:
            committed = []
        rows.append(
            {
                "path": str(item.path),
                "branch": item.branch or "",
                "changed": len(files),
                "files": files,
                "committed": committed,
                "head": item.head[:7],
            }
        )
    return rows


def _head(repo: Path) -> tuple[str, str, bool]:
    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).stdout.strip()
    head = run_git(["rev-parse", "--short", "HEAD"], cwd=repo).stdout.strip()
    clean = not run_git(["status", "--porcelain"], cwd=repo).stdout.strip()
    return branch, head, clean


def _events(path: Path) -> list[dict[str, Any]]:
    from treefleet.events import read_events

    rows, _skipped = read_events(path)
    return rows


def _stage(events: list[dict[str, Any]], waiting: bool, finished: bool) -> str:
    if finished:
        return "done"
    if waiting:
        return "approve"
    names = [str(item.get("type")) for item in events]

    def phase(name: str) -> bool:
        return any(item.get("type") == "phase" and (item.get("payload") or {}).get("name") == name for item in events)

    if any(item.startswith("pr.") for item in names):
        return "pr"
    if "review_started" in names or phase("review"):
        return "integrate" if phase("integrate") else "review"
    fleet = any(item.get("type") in {"worker.spawned", "worker.exited"} for item in events)
    fleet = fleet or any(
        item.get("type") == "phase" and (item.get("payload") or {}).get("name") == "fleet" for item in events
    )
    if fleet:
        return "fleet"
    if any(item.get("type") == "phase" and item.get("payload", {}).get("name") == "planner" for item in events):
        return "plan"
    return "guard"


class Board:
    def __init__(self, repo: Path, settings: Settings, token: str, runs: Path) -> None:
        self.repo = repo
        self.settings = settings
        self.token = token
        self.runs = runs
        self.approvals: dict[str, Approval] = {}
        self.started: dict[str, float] = {}
        self.finished: set[str] = set()
        self.ended: dict[str, float] = {}
        self.messages: dict[str, str] = {}
        self.goals: dict[str, str] = {}
        self.worker_models: dict[str, str] = {}
        self.replay = False
        self.dead = False
        self.replay_events: list[dict[str, Any]] = []
        self.base_sha = run_git(["rev-parse", "HEAD"], cwd=repo).stdout.strip() or "HEAD"
        self._lock = threading.Lock()

    def launch(self, goal: str, worker_model: str, plan_approval: bool) -> str:
        run_id = secrets.token_hex(4)
        self.base_sha = run_git(["rev-parse", "HEAD"], cwd=self.repo).stdout.strip() or self.base_sha
        approval = Approval()
        with self._lock:
            self.approvals[run_id] = approval
            self.started[run_id] = time.time()
            self.goals[run_id] = goal
            self.worker_models[run_id] = worker_model

        def work() -> None:
            from treefleet.checks import detect_checks, select_checks
            from treefleet.orchestrator.pipeline import run_pipeline

            detected = detect_checks(self.repo)
            selected = select_checks(self.repo, detected, worktree_root=Path(self.settings.worktree_root))
            fakes = _stub_fakes()
            request = RunRequest(
                goal=goal,
                repo=str(self.repo),
                worker_model=worker_model,
                plan_approval=plan_approval,
                checks={} if fakes is not None else selected.checks,
                notes=[] if fakes is not None else list(selected.notes),
            )
            if fakes is not None:
                time.sleep(1.0)
            outcome = asyncio.run(
                run_pipeline(
                    request,
                    self.settings,
                    approve=approval if plan_approval else None,
                    run_id=run_id,
                    runs_root=self.runs,
                    fakes=fakes,
                )
            )
            with self._lock:
                self.finished.add(run_id)
                self.ended[run_id] = time.time()
                self.messages[run_id] = outcome.message

        threading.Thread(target=work, name=f"tf-{run_id}", daemon=True).start()
        return run_id

    def snapshot(self, run_id: str) -> dict[str, Any]:
        approval = self.approvals.get(run_id)
        waiting = bool(approval and approval.waiting())
        events = list(self.replay_events) if self.replay else _events(self.runs / run_id / "events.jsonl")
        plan = None
        if approval and approval.plan is not None:
            plan = [
                {"id": task.id, "title": task.title, "allowed_paths": list(task.allowed_paths)}
                for task in approval.plan.tasks
            ]
        report_path = self.runs / run_id / "report.json"
        report: dict[str, Any] | None = None
        if report_path.is_file():
            try:
                loaded = json.loads(report_path.read_text(encoding="utf-8"))
                report = loaded if isinstance(loaded, dict) and loaded.get("schema_id") else None
            except json.JSONDecodeError:
                report = None
        branch, head, clean = _head(self.repo)
        tasks = tasks_from_snapshot(events, report, awaiting=waiting)
        goal = self.goals.get(run_id, "")
        if not goal and isinstance(report, dict):
            goal = str(report.get("goal") or "")
        body = {
            "repo": str(self.repo),
            "branch": branch,
            "head": head,
            "clean": clean,
            "replay": self.replay,
            "dead": self.dead,
            "run_id": run_id,
            "goal": goal,
            "worker_model": self.worker_models.get(run_id, "router"),
            "stage": _stage(events, waiting, run_id in self.finished or report is not None),
            "started_at": self.started.get(run_id, time.time()),
            "finished_at": self.ended.get(run_id),
            "awaiting": plan if waiting else None,
            "events": events[-400:],
            "tasks": tasks,
            "needs_human": run_needs_human(events, report, awaiting=waiting),
            "worktrees": rows_for_run(worktree_rows(self.repo, self.base_sha), run_id),
            "report": report,
            "message": self.messages.get(run_id, ""),
            "stages": list(_STAGES),
            "runs": self.run_catalog(),
        }
        if any(str(item.get("type") or "").startswith("pr.") for item in events):
            body["pr_mode"] = True
            body["pr_repo"] = self.settings.pr.repo
        clean_body, _count = redact_payload(body)
        return clean_body if isinstance(clean_body, dict) else body

    def run_catalog(self) -> list[dict[str, float | str]]:
        """In-process runs, then any run directory, newest first."""
        rows: dict[str, float] = dict(self.started)
        if self.runs.is_dir():
            for path in self.runs.glob("*/run.json"):
                try:
                    rows.setdefault(path.parent.name, path.stat().st_mtime)
                except OSError:
                    continue
        ordered = sorted(rows, key=lambda key: rows[key], reverse=True)
        return [{"id": key, "started": rows[key]} for key in ordered]


def create_app(
    repo: Path,
    *,
    root: Path | None = None,
    token: str | None = None,
    fixture: dict[str, Any] | None = None,
    replay: Path | None = None,
    models: list[dict[str, str]] | None = None,
    runs: Path | None = None,
) -> FastAPI:
    base = root or Path.cwd()
    settings = load_settings(base / "treefleet.toml")
    board = Board(repo, settings, token or secrets.token_urlsafe(18), runs or base / "runs")
    app = FastAPI()
    app.state.board = board
    app.state.fixture = fixture
    app.state.models = models
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    if replay is not None:
        board.replay = True
        record_path = replay.parent / "run.json"
        if record_path.is_file():
            from treefleet.sessions import load_run, run_state

            try:
                board.dead = run_state(load_run(record_path)) == "dead"
            except (OSError, ValueError):
                board.dead = False
        from treefleet.events import read_events, skip_message

        recorded, skipped = read_events(replay)
        if skipped:
            print(skip_message(skipped), flush=True)

        def play() -> None:
            previous: str | None = None
            for item in recorded:
                stamp = str(item.get("ts") or "")
                # ponytail: a gap over 30s plays at 30s so a bad timestamp cannot stall the demo
                time.sleep(replay_gap(previous, stamp))
                previous = stamp or previous
                board.replay_events.append(item)
            board.finished.add(str(recorded[-1].get("run_id")) if recorded else "replay")

        threading.Thread(target=play, name="tf-replay", daemon=True).start()

    @app.middleware("http")
    async def _local(request: Request, call_next):  # type: ignore[no-untyped-def]
        if not local_host(request.headers.get("host", "")):
            return JSONResponse({"error": "local only"}, status_code=403)
        if request.url.path.startswith("/api"):
            header = request.headers.get("authorization", "")
            if header != f"Bearer {board.token}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        page = _PAGE.read_text(encoding="utf-8")
        page = page.replace("{{TOKEN}}", board.token)
        page = page.replace("{{REPLAY}}", "true" if board.replay else "false")
        page = page.replace("{{REPLAY_ATTR}}", "" if board.replay else "hidden")
        if board.dead:
            page = page.replace(">REPLAY<", ">DEAD<", 1)
        return HTMLResponse(page)

    @app.get("/api/models")
    def models_route() -> JSONResponse:
        return JSONResponse(_model_options(settings, app.state.models))

    @app.get("/api/worktrees")
    def worktrees() -> JSONResponse:
        raw_rows = fixture.get("worktrees") if fixture is not None else None
        rows = raw_rows if isinstance(raw_rows, list) and raw_rows else worktree_rows(repo, board.base_sha)
        run_id = next(iter(board.approvals), "")
        if board.replay and board.replay_events:
            run_id = str(board.replay_events[-1].get("run_id") or run_id)
        if run_id and fixture is None:
            rows = rows_for_run(list(rows), run_id)
        body, _count = redact_payload({"worktrees": rows})
        return JSONResponse(body)

    @app.get("/api/board")
    def current(run: str = "") -> JSONResponse:
        if fixture is not None:
            body, _count = redact_payload(fixture)
            return JSONResponse(body)
        run_id = run
        if not run_id and board.replay and board.replay_events:
            run_id = str(board.replay_events[-1].get("run_id") or "")
        if not run_id:
            catalog = board.run_catalog()
            run_id = str(catalog[0]["id"]) if catalog else ""
        if not run_id:
            branch, head, clean = _head(repo)
            return JSONResponse(
                {
                    "repo": str(repo),
                    "branch": branch,
                    "head": head,
                    "clean": clean,
                    "replay": board.replay,
                    "dead": board.dead,
                    "stage": "guard",
                    "stages": list(_STAGES),
                    "events": list(board.replay_events),
                    "tasks": [],
                    "needs_human": False,
                    "worktrees": worktree_rows(repo, board.base_sha),
                    "awaiting": None,
                    "report": None,
                    "runs": [],
                }
            )
        return JSONResponse(board.snapshot(run_id))

    @app.post("/api/runs")
    async def start(request: Request) -> JSONResponse:
        if board.replay:
            return JSONResponse({"error": "replay is not a live run"}, status_code=409)
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"error": "bad request"}, status_code=400)
        goal = str(payload.get("goal") or "")
        raw_repo = str(payload.get("repo") or repo)
        if deterministic_reasons(goal) or deterministic_reasons(raw_repo):
            return JSONResponse({"error": "input blocked"}, status_code=400)
        target = Path(raw_repo)
        if not (target / ".git").exists():
            return JSONResponse({"error": "not a git repository"}, status_code=400)
        screen = screen_goal(goal, settings, None)
        if not screen.decision.allowed:
            return JSONResponse({"error": "input blocked"}, status_code=400)
        model = str(payload.get("worker_model") or "router")
        approval = bool(payload.get("plan_approval", True))
        run_id = board.launch(goal, model, approval)
        return JSONResponse({"run_id": run_id})

    @app.get("/api/diff")
    def diff(path: str) -> JSONResponse:
        from treefleet.git.worktrees import list_worktrees

        try:
            target = Path(path).resolve()
            allowed = {item.path.resolve() for item in list_worktrees(repo)}
        except (OSError, ValueError, RuntimeError):
            return JSONResponse({"error": "unknown worktree"}, status_code=404)
        if target not in allowed or target.resolve() == repo.resolve():
            return JSONResponse({"error": "unknown worktree"}, status_code=404)
        from treefleet.git.proofs import diff_stats

        base = board.base_sha or "HEAD"
        full = run_git(["diff", base], cwd=target)
        text = full.stdout or ""
        try:
            files = [change.model_dump() for change in diff_stats(target, base)]
        except Exception:
            files = []
        extra = [item["path"] for item in files if f"diff --git a/{item['path']}" not in text]
        if extra:
            text += "\nUntracked:\n" + "\n".join(extra)
        body, _count = redact_payload({"diff": text[:8000], "files": files})
        return JSONResponse(body)

    @app.post("/api/runs/{run_id}/approve")
    def approve(run_id: str, request: Request) -> JSONResponse:
        gate = board.approvals.get(run_id)
        if gate is None:
            return JSONResponse({"error": "unknown run"}, status_code=404)
        client = request.headers.get("x-treefleet-client", "")
        gate.finish(True, "ui" if client.strip().lower() == "ui" else "http")
        return JSONResponse({"ok": True, "source": gate.source})

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str) -> JSONResponse:
        gate = board.approvals.get(run_id)
        if gate is None:
            return JSONResponse({"error": "unknown run"}, status_code=404)
        gate.finish(False)
        return JSONResponse({"ok": True})

    @app.get("/api/runs/{run_id}/events")
    def events(run_id: str, request: Request) -> StreamingResponse:
        last = request.headers.get("last-event-id")
        seen = int(last) if last and last.isdigit() else -1

        def generate():
            path = board.runs / run_id / "events.jsonl"
            if not board.replay and run_id not in board.approvals and not path.is_file():
                return
            from treefleet.events import read_events, skip_message

            cursor = seen
            quiet = 0
            announced = False
            while quiet < 3:
                if board.replay:
                    rows, skipped = list(board.replay_events), 0
                else:
                    rows, skipped = read_events(board.runs / run_id / "events.jsonl")
                if skipped and not announced:
                    print(skip_message(skipped), flush=True)
                    announced = True
                emitted = False
                for item in rows:
                    seq = int(item.get("seq") or 0)
                    if seq <= cursor:
                        continue
                    cursor = seq
                    emitted = True
                    line, _count = redact_payload(item)
                    yield f"id: {seq}\ndata: {json.dumps(line)}\n\n"
                if run_id in board.finished and not emitted:
                    quiet += 1
                else:
                    quiet = 0
                time.sleep(0.4)

        return StreamingResponse(generate(), media_type="text/event-stream")

    return app


def _model_options(settings: Settings, override: list[dict[str, str]] | None) -> list[dict[str, str]]:
    options = [{"id": "router", "label": "Smart routing"}, {"id": "auto", "label": "Cursor Auto"}]
    if override is not None:
        for item in override:
            if item["id"] not in {"router", "auto"}:
                options.append(item)
        return options
    from treefleet.cursor_models import UnknownModelError, discover

    try:
        found = discover(Path(settings.cursor.cli), settings.cursor.list_flag)
    except (UnknownModelError, OSError):
        return options
    for model in found:
        if model.id not in {"router", "auto"}:
            options.append({"id": model.id, "label": model.label or model.id})
    return options
