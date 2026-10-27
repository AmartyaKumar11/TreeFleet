"""Planner, router, scan, verifier and reporter. Fleet lives in fleet.py."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any, Literal

from treefleet.config import Settings
from treefleet.contracts import (
    FinalReport,
    IsolationReport,
    Plan,
    RouteDecision,
    RunEvent,
    RunMetrics,
    RunRequest,
    StateKey,
    WorkerResult,
)
from treefleet.events import EventSink
from treefleet.git.proofs import (
    MainSnapshot,
    SiblingSnapshot,
    snapshot_main,
    snapshot_siblings,
    verify_main,
    verify_siblings,
    verify_task_isolation,
)
from treefleet.git.worktrees import git_out

_PLAN_INSTRUCTION = (
    "Return one JSON object for a Plan and nothing else. "
    "No markdown. Fields: goal, rationale, tasks. "
    "Each task has id, title, instructions, allowed_paths, and optional depends_on, "
    "complexity, and model_override. complexity is trivial, moderate, or complex. Ids are lowercase slugs."
)

_COMPLEXITY_ALIASES = {"low": "trivial", "simple": "trivial", "medium": "moderate", "high": "complex"}


def _normalize_plan(text: str) -> str:
    """Map low/medium/high onto the contract values. Other text is left for validation."""
    raw = _json_text(text)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list):
        return raw
    for task in tasks:
        if not isinstance(task, dict):
            continue
        value = task.get("complexity")
        if isinstance(value, str):
            mapped = _COMPLEXITY_ALIASES.get(value.strip().lower())
            if mapped:
                task["complexity"] = mapped
    return json.dumps(data)


class RunBundle:
    """Mutable handles for one run. Agents share this object. It is not session state."""

    def __init__(
        self,
        *,
        request: RunRequest,
        settings: Settings,
        repo: Path,
        run_id: str,
        token: str,
        sink: EventSink,
        runs_root: Path,
        worktree_root: Path,
        port_start: int,
        approve: Callable[[Plan], bool] | None,
        fakes: Any,
    ) -> None:
        self.request = request
        self.settings = settings
        self.repo = repo
        self.run_id = run_id
        self.token = token
        self.sink = sink
        self.runs_root = runs_root
        self.worktree_root = worktree_root
        self.port_start = port_start
        self.approve = approve
        self.fakes = fakes
        self.approved = True
        self.main_before: MainSnapshot | None = None
        self.siblings_before: tuple[SiblingSnapshot, ...] = ()
        self.procs: list[Any] = []
        self.ports: set[int] = set()
        self.started = 0.0
        self.notes: list[str] = []


def repo_map(repo: Path) -> str:
    files = [line.strip() for line in git_out(["ls-files"], repo).splitlines() if line.strip()]
    return "\n".join(files[:200])


def resolve_cursor_model(
    task_override: str | None, request: RunRequest, settings: Settings
) -> tuple[str | None, Literal["override", "run", "router", "default"]]:
    """Phase 1 order. Until Phase 5, router means models.router.default_tier."""
    if task_override:
        return task_override, "override"
    if request.worker_model != "router":
        return request.worker_model, "run"
    chosen = settings.models.router.fallback()
    if chosen:
        return chosen, "router"
    if settings.models.default:
        return settings.models.default, "default"
    return None, "default"


def _json_text(text: str) -> str:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        raw = raw.rsplit("```", 1)[0]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return raw[start : end + 1]
    return raw


async def _complete(llm: Any, user_text: str) -> str:
    """One LlmAgent turn. output_schema stays unset (S1)."""
    from google.adk.agents import LlmAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    agent = LlmAgent(
        name="planner",
        model=llm,
        instruction=_PLAN_INSTRUCTION,
        output_key="plan",
    )
    service = InMemorySessionService()
    await service.create_session(app_name="planner", user_id="u", session_id="s")
    runner = Runner(app_name="planner", agent=agent, session_service=service)
    message = types.Content(role="user", parts=[types.Part(text=user_text)])
    text = ""
    async for event in runner.run_async(user_id="u", session_id="s", new_message=message):
        content = getattr(event, "content", None)
        if content is None:
            continue
        for part in content.parts or []:
            if getattr(part, "text", None):
                text = part.text
    session = await service.get_session(app_name="planner", user_id="u", session_id="s")
    stored = ""
    if session is not None:
        stored = str(session.state.get("plan") or "")
    return stored or text


async def plan_goal(llm: Any, goal: str, tree: str) -> Plan:
    """Parse Plan JSON. One repair retry sends the validation error back."""
    from pydantic import ValidationError

    prompt = f"Goal:\n{goal}\n\nRepository files:\n{tree}"
    text = await _complete(llm, prompt)
    try:
        return Plan.model_validate_json(_normalize_plan(text))
    except (ValidationError, json.JSONDecodeError) as exc:
        repair = (
            f"The previous JSON failed validation:\n{exc}\n"
            "complexity is trivial, moderate, or complex. Return only the corrected JSON object.\n\n"
            f"{prompt}"
        )
        text = await _complete(llm, repair[:4000])
        return Plan.model_validate_json(_normalize_plan(text))


def route_plan(plan: Plan, request: RunRequest, settings: Settings) -> list[RouteDecision]:
    decisions: list[RouteDecision] = []
    for task in plan.tasks:
        cursor, source = resolve_cursor_model(task.model_override, request, settings)
        decisions.append(
            RouteDecision(
                task_id=task.id,
                model=cursor or settings.models.fast,
                complexity=task.complexity,
                confidence=1.0,
                source=source,
                cursor_model=cursor,
            )
        )
    return decisions


def prove(
    bundle: RunBundle,
    plan: Plan,
    results: dict[str, WorkerResult],
) -> IsolationReport:
    owned: set[Path] = set()
    proofs = []
    by_id = {task.id: task for task in plan.tasks}
    for task_id, _result in results.items():
        task = by_id[task_id]
        worktree = bundle.worktree_root / bundle.run_id / task_id
        proc = next((item for item in bundle.procs if item.task_id == task_id), None)
        pid = proc.pid if proc is not None else None
        if worktree.exists():
            owned.add(worktree)
        proofs.append(
            verify_task_isolation(
                bundle.repo,
                task_id=task_id,
                worktree=worktree,
                run_id=bundle.run_id,
                base_ref=bundle.request.base_ref,
                allowed_paths=list(task.allowed_paths),
                denied_paths=list(task.denied_paths),
                pid=pid,
            )
        )
    main = verify_main(bundle.repo, bundle.main_before or snapshot_main(bundle.repo))
    siblings = verify_siblings(bundle.siblings_before, snapshot_siblings(bundle.repo), owned)
    if not siblings.ok:
        bundle.notes.append("sibling worktree changed: " + ", ".join(siblings.offending))
    return IsolationReport(tasks=proofs, main=main)


def build_report(
    bundle: RunBundle,
    plan: Plan,
    results: list[WorkerResult],
    isolation: IsolationReport,
    routing: list[RouteDecision],
    wall_seconds: float,
) -> FinalReport:
    sequential = sum(item.duration_s for item in results)
    return FinalReport(
        run_id=bundle.run_id,
        goal=bundle.request.goal,
        plan=plan,
        results=results,
        verdicts=[],
        isolation=isolation,
        routing=routing,
        metrics=RunMetrics(wall_seconds=wall_seconds, sequential_seconds=sequential),
    )


def state_delta(key: str, value: Any) -> dict[str, Any]:
    return {key: value}


def dump_plan(plan: Plan) -> dict[str, Any]:
    return plan.model_dump(mode="json")


def dump_results(results: dict[str, WorkerResult]) -> dict[str, Any]:
    return {key: value.model_dump(mode="json") for key, value in results.items()}


def read_plan(state: dict[str, Any]) -> Plan:
    return Plan.model_validate(state[StateKey.PLAN])


def read_routing(state: dict[str, Any]) -> list[RouteDecision]:
    return [RouteDecision.model_validate(item) for item in state.get(StateKey.ROUTING, [])]


def read_results(state: dict[str, Any]) -> dict[str, WorkerResult]:
    raw = state.get(StateKey.WORKER_RESULTS, {})
    return {key: WorkerResult.model_validate(value) for key, value in raw.items()}


def _adk_event(ctx: Any, author: str, delta: dict[str, Any]) -> Any:
    from google.adk.events.event import Event
    from google.adk.events.event_actions import EventActions

    return Event(
        invocation_id=ctx.invocation_id,
        author=author,
        branch=ctx.branch,
        actions=EventActions(state_delta=delta),
    )


def planner_llm(bundle: RunBundle) -> Any:
    if bundle.fakes is not None and bundle.fakes.planner is not None:
        return bundle.fakes.planner
    from google.adk.models.lite_llm import LiteLlm

    return LiteLlm(model=bundle.settings.models.strong)


def _base_agent():
    from google.adk.agents import BaseAgent

    return BaseAgent


BaseAgent = _base_agent()


class RepoScanAgent(BaseAgent):
    bundle: Any

    async def _run_async_impl(self, ctx: Any) -> AsyncGenerator[Any, None]:
        text = repo_map(self.bundle.repo)
        yield _adk_event(
            ctx,
            self.name,
            {
                StateKey.REQUEST: self.bundle.request.model_dump(mode="json"),
                StateKey.REPO_MAP: text,
            },
        )


class PlannerAgent(BaseAgent):
    bundle: Any

    async def _run_async_impl(self, ctx: Any) -> AsyncGenerator[Any, None]:
        tree = str(ctx.session.state.get(StateKey.REPO_MAP, ""))
        plan = await plan_goal(planner_llm(self.bundle), self.bundle.request.goal, tree)
        approved = True
        if self.bundle.request.plan_approval:
            approved = True if self.bundle.approve is None else bool(self.bundle.approve(plan))
            self.bundle.sink.emit_sync(
                RunEvent(
                    run_id=self.bundle.run_id,
                    seq=0,
                    source="orchestrator",
                    type="awaiting_approval",
                    payload={"approved": approved},
                )
            )
        self.bundle.approved = approved
        yield _adk_event(ctx, self.name, {StateKey.PLAN: dump_plan(plan), "approved": approved})


class RouterAgent(BaseAgent):
    bundle: Any

    async def _run_async_impl(self, ctx: Any) -> AsyncGenerator[Any, None]:
        if not self.bundle.approved:
            return
        plan = read_plan(ctx.session.state)
        routing = route_plan(plan, self.bundle.request, self.bundle.settings)
        for decision in routing:
            self.bundle.sink.emit_sync(
                RunEvent(
                    run_id=self.bundle.run_id,
                    seq=0,
                    source="orchestrator",
                    type="model_selected",
                    task_id=decision.task_id,
                    payload={
                        "task_id": decision.task_id,
                        "model": decision.cursor_model or "",
                        "source": decision.source,
                        "complexity": decision.complexity,
                    },
                )
            )
        yield _adk_event(
            ctx,
            self.name,
            {StateKey.ROUTING: [item.model_dump(mode="json") for item in routing]},
        )


class VerifierAgent(BaseAgent):
    bundle: Any

    async def _run_async_impl(self, ctx: Any) -> AsyncGenerator[Any, None]:
        if not self.bundle.approved:
            return
        plan = read_plan(ctx.session.state)
        results = read_results(ctx.session.state)
        isolation = prove(self.bundle, plan, results)
        yield _adk_event(ctx, self.name, {StateKey.ISOLATION: isolation.model_dump(mode="json")})


class ReporterAgent(BaseAgent):
    bundle: Any

    async def _run_async_impl(self, ctx: Any) -> AsyncGenerator[Any, None]:
        if not self.bundle.approved:
            return
        import time

        plan = read_plan(ctx.session.state)
        results = read_results(ctx.session.state)
        isolation = IsolationReport.model_validate(ctx.session.state[StateKey.ISOLATION])
        routing = read_routing(ctx.session.state)
        wall = max(0.0, time.monotonic() - self.bundle.started)
        report = build_report(self.bundle, plan, list(results.values()), isolation, routing, wall)
        folder = self.bundle.runs_root / self.bundle.run_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        (folder / "report.md").write_text(report.to_markdown(), encoding="utf-8")
        yield _adk_event(ctx, self.name, {StateKey.REPORT: report.model_dump(mode="json")})
