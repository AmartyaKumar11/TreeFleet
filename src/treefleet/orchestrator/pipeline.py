"""One run: clean checkout, ADK pipeline, proofs, report. Fakes enter only through OfflineFakes."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from treefleet.config import Settings
from treefleet.contracts import FinalReport, Plan, RunRequest
from treefleet.events import EventSink
from treefleet.git.canary import CANARY_NAME
from treefleet.git.worktrees import GitError, kill_tree, list_worktrees, prepare_repo, remove_worktree
from treefleet.orchestrator.agents import (
    PlannerAgent,
    PlanRejected,
    ReporterAgent,
    RepoScanAgent,
    RouterAgent,
    RunBundle,
    VerifierAgent,
)
from treefleet.orchestrator.fleet import FleetAgent


@dataclass
class OfflineFakes:
    """Test-only stand-ins. The CLI never builds this."""

    planner: Any
    cursor_cli: Path
    mock_llm: str = "1"
    fake_edit: str = "readme"
    fake_sleep: str = "0"
    kill_task: str | None = None


@dataclass
class RunOutcome:
    code: int
    report: FinalReport | None
    state: dict[str, Any]
    run_id: str
    message: str = ""


class LiveSink(EventSink):
    def emit_sync(self, event):  # type: ignore[override]
        stored = super().emit_sync(event)
        if stored.type == "model_selected":
            print(f"model {stored.task_id} {stored.payload.get('model')}", flush=True)
        elif stored.type == "worker.exited":
            files = stored.payload.get("files") or []
            shown = ", ".join(files) if isinstance(files, list) else ""
            print(f"status {stored.task_id} {stored.payload.get('status')} files {shown}", flush=True)
        return stored


def cleanup_run(repo: Path, run_id: str) -> list[str]:
    """Remove this run's worktrees and its canary. Branches stay."""
    removed: list[str] = []
    prefix = f"tf/{run_id}/"
    for item in list_worktrees(repo):
        if item.branch and item.branch.startswith(prefix):
            remove_worktree(repo, item.path)
            removed.append(str(item.path))
    canary = repo / CANARY_NAME
    if canary.is_file() and run_id in canary.read_text(encoding="utf-8"):
        canary.unlink()
    return removed


async def run_pipeline(
    request: RunRequest,
    settings: Settings,
    *,
    approve: Any = None,
    fakes: OfflineFakes | None = None,
    runs_root: Path | None = None,
    worktree_root: Path | None = None,
    port_start: int | None = None,
) -> RunOutcome:
    repo = Path(request.repo)
    try:
        prepare_repo(repo)
    except GitError as exc:
        text = str(exc)
        if "not clean" in text:
            text = f"dirty checkout: {text}"
        return RunOutcome(2, None, {}, "", text)

    from google.adk.agents import SequentialAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    run_id = secrets.token_hex(4)
    root = Path(runs_root) if runs_root is not None else Path.cwd() / "runs"
    sink = LiveSink(root)
    bundle = RunBundle(
        request=request,
        settings=settings,
        repo=repo,
        run_id=run_id,
        token=secrets.token_urlsafe(24),
        sink=sink,
        runs_root=root,
        worktree_root=Path(worktree_root) if worktree_root is not None else Path(settings.worktree_root),
        port_start=port_start if port_start is not None else settings.workers.port_start,
        approve=approve,
        fakes=fakes,
    )
    bundle.started = time.monotonic()
    pipeline = SequentialAgent(
        name="pipeline",
        sub_agents=[
            RepoScanAgent(name="repo_scan", bundle=bundle),
            PlannerAgent(name="planner", bundle=bundle),
            RouterAgent(name="router", bundle=bundle),
            FleetAgent(name="fleet", bundle=bundle),
            VerifierAgent(name="verifier", bundle=bundle),
            ReporterAgent(name="reporter", bundle=bundle),
        ],
    )
    service = InMemorySessionService()
    await service.create_session(app_name="treefleet", user_id="u", session_id=run_id)
    runner = Runner(app_name="treefleet", agent=pipeline, session_service=service)
    message = types.Content(role="user", parts=[types.Part(text=request.goal)])
    try:
        async for _event in runner.run_async(user_id="u", session_id=run_id, new_message=message):
            pass
    except PlanRejected as exc:
        return RunOutcome(1, None, {}, run_id, str(exc))
    finally:
        for proc in bundle.procs:
            kill_tree(proc.popen.pid)
            try:
                proc.popen.wait(timeout=10)
            except Exception:
                pass
    if not bundle.approved:
        cleanup_run(repo, run_id)
        return RunOutcome(0, None, {}, run_id, "plan declined")
    session = await service.get_session(app_name="treefleet", user_id="u", session_id=run_id)
    state = dict(session.state) if session is not None else {}
    report = FinalReport.model_validate(state["report"]) if "report" in state else None
    if report is None:
        return RunOutcome(1, None, state, run_id, "run finished without a report")
    path = root / run_id / "report.md"
    print(path, flush=True)
    return RunOutcome(0, report, state, run_id, "")


def ask_plan(plan: Plan) -> bool:
    print(plan.model_dump_json())
    answer = input("Approve plan? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def run_blocking(request: RunRequest, settings: Settings, *, approve: Any) -> RunOutcome:
    return asyncio.run(run_pipeline(request, settings, approve=approve))
