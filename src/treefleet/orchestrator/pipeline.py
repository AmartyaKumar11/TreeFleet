"""One run: clean checkout, ADK pipeline, proofs, report. Fakes enter only through OfflineFakes."""

from __future__ import annotations

import asyncio
import json
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
from treefleet.orchestrator.review import IntegratorAgent, ReviewerAgent


@dataclass
class OfflineFakes:
    """Test-only stand-ins. The CLI never builds this."""

    planner: Any
    cursor_cli: Path
    mock_llm: str = "1"
    fake_edit: str = "readme"
    fake_sleep: str = "0"
    kill_task: str | None = None
    skip_jev: bool = True
    review_script: list[str] | None = None


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
            raw = stored.payload.get("files") or []
            files = raw if isinstance(raw, list) else []
            names = [item.get("path", "") if isinstance(item, dict) else str(item) for item in files]
            shown = ", ".join(names)
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
    run_id: str | None = None,
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

    run_id = run_id or secrets.token_hex(4)
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
    bundle.jev = _jev_for(bundle)
    blocked = _screen_input(bundle)
    if blocked is not None:
        return blocked
    from treefleet.orchestrator.callbacks import agent_hooks

    def _agent(cls: type, name: str) -> Any:
        before, after = agent_hooks(bundle.sink, bundle.run_id, name)
        return cls(name=name, bundle=bundle, before_agent_callback=before, after_agent_callback=after)

    pipeline = SequentialAgent(
        name="pipeline",
        sub_agents=[
            _agent(RepoScanAgent, "repo_scan"),
            _agent(PlannerAgent, "planner"),
            _agent(RouterAgent, "router"),
            _agent(FleetAgent, "fleet"),
            _agent(VerifierAgent, "verifier"),
            _agent(ReviewerAgent, "reviewer"),
            _agent(IntegratorAgent, "integrator"),
            _agent(ReporterAgent, "reporter"),
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
        _write_blocked(root, run_id, request.goal, "Plan rejected", str(exc))
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


def _jev_for(bundle: RunBundle) -> Any:
    if not bundle.request.use_jev:
        return None
    if bundle.fakes is not None and bundle.fakes.skip_jev:
        return None
    from treefleet.jev.client import JevClient

    return JevClient(bundle.settings.typesafe_api_key, bundle.settings.pricing.jev_source_url)


def _screen_input(bundle: RunBundle) -> RunOutcome | None:
    from treefleet.contracts import RunEvent
    from treefleet.guards.input import screen_goal

    screen = screen_goal(bundle.request.goal, bundle.settings, bundle.jev)
    bundle.sink.emit_sync(
        RunEvent(
            run_id=bundle.run_id,
            seq=0,
            source="guard",
            type="guard.decision",
            payload=screen.decision.model_dump(mode="json"),
        )
    )
    if screen.degraded:
        bundle.sink.emit_sync(
            RunEvent(
                run_id=bundle.run_id,
                seq=0,
                source="jev",
                type="guard_degraded",
                payload={"stage": "input", "detector": "deterministic"},
            )
        )
    if screen.decision.allowed:
        return None
    reason = "; ".join(screen.decision.reasons) or "input blocked"
    _write_blocked(bundle.runs_root, bundle.run_id, bundle.request.goal, "Input blocked", reason)
    return RunOutcome(1, None, {}, bundle.run_id, f"input blocked: {reason}")


def _write_blocked(root: Path, run_id: str, goal: str, title: str, reason: str) -> None:
    from treefleet.guards.text import redact

    folder = root / run_id
    folder.mkdir(parents=True, exist_ok=True)
    markdown, _count = redact(f"# TreeFleet run {run_id}\n\n**{title}**\n\n{reason}\n")
    body, _count = redact(json.dumps({"run_id": run_id, "goal": goal, "title": title, "reason": reason}))
    (folder / "report.md").write_text(markdown, encoding="utf-8")
    (folder / "report.json").write_text(body, encoding="utf-8")


def ask_plan(plan: Plan) -> bool:
    print(plan.model_dump_json())
    answer = input("Approve plan? [y/N] ")
    return answer.strip().lower() in {"y", "yes"}


def run_blocking(request: RunRequest, settings: Settings, *, approve: Any) -> RunOutcome:
    return asyncio.run(run_pipeline(request, settings, approve=approve))
