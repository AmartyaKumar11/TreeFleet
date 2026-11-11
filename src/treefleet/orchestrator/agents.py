"""Planner, router, scan, verifier and reporter. Fleet lives in fleet.py."""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncGenerator, Callable
from pathlib import Path
from typing import Any, Literal

from treefleet.checks import detect_checks, forbidden_stem
from treefleet.config import Settings
from treefleet.contracts import (
    Complexity,
    FinalReport,
    GuardDecision,
    GuardStage,
    IntegrationReport,
    IsolationReport,
    Plan,
    ReviewVerdict,
    RouteDecision,
    RunEvent,
    RunRequest,
    StateKey,
    TokenUsage,
    WorkerResult,
    assemble_metrics,
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
from treefleet.guards.cost import collect_costs
from treefleet.guards.paths import path_violation
from treefleet.guards.text import redact

_PLAN_INSTRUCTION = (
    "Return one JSON object for a Plan and nothing else. No markdown. "
    "Fields: goal, rationale, tasks. "
    "Each task has id, title, instructions, allowed_paths, and optional depends_on, "
    "check_ids, complexity, and model_override. "
    "complexity is trivial, moderate, or complex. Ids are lowercase slugs. "
    "Every task edits at least one file. allowed_paths names the files that task edits. "
    "Do not add a task that only inspects, reviews, explores, reads, investigates, or runs tests or commands. "
    "Tests and lint run as checks after the edit. Put the relevant check names in check_ids. "
    "Leave depends_on empty so independent tasks share one wave."
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
        self.store: Any = None
        self.approved = True
        self.main_before: MainSnapshot | None = None
        self.siblings_before: tuple[SiblingSnapshot, ...] = ()
        self.procs: list[Any] = []
        self.ports: set[int] = set()
        self.started = 0.0
        self.fleet_started = 0.0
        self.fleet_ended = 0.0
        self.phases: dict[str, float] = {}
        self.notes: list[str] = []
        self.planner_usage = TokenUsage()
        self.planner_model = ""
        self.jev: Any = None
        self.jev_calls = 0
        self.jev_tokens = 0
        self.workers: dict[str, Any] = {}
        self.envelopes: dict[str, Any] = {}
        self.reviewer_usages: list[Any] = []
        self.review_seconds = 0.0
        self.revise_seconds = 0.0
        self.integration_seconds = 0.0
        # Injected by tests. Production leaves these empty and uses gh, git, and the real reviewer.
        self.pr_provider: Any = None
        self.pr_push: Any = None
        self.pr_revise: Any = None
        self.pr_resolve: Any = None
        self.pr_judge: Any = None
        self.pr_diff: Any = None
        self.pr_sleep: Any = None
        self.pr_now: Any = None
        self.pr_deadline: float | None = None
        self.pr_login: str = ""
        self.pr_hook_hosts: list[str] = []


def _phase(bundle: RunBundle, name: str, duration_s: float) -> None:
    duration = round(duration_s, 3)
    bundle.phases[name] = duration
    bundle.sink.emit_sync(
        RunEvent(
            run_id=bundle.run_id,
            seq=0,
            source="orchestrator",
            type="phase",
            payload={"name": name, "duration_s": duration},
        )
    )


def repo_map(repo: Path) -> str:
    files = [line.strip() for line in git_out(["ls-files"], repo).splitlines() if line.strip()]
    return "\n".join(files[:200])


def scan_text(repo: Path) -> str:
    """File list plus the commands RepoScan detected. The CLI confirms which ones run."""
    files = repo_map(repo)
    found = detect_checks(repo)
    if not found:
        return files
    lines = "\n".join(f"{name}: {command}" for name, command in found.items())
    return f"{files}\n\nDetected checks:\n{lines}"


class PlanRejected(RuntimeError):
    """The planner returned a read-only task twice."""


class PlannerAuthError(RuntimeError):
    """The planner's provider rejected the key. The message is the only line a run prints."""

    def __init__(self) -> None:
        super().__init__("DeepSeek authentication failed: check the process environment variable; it overrides .env")


def _auth_status(exc: BaseException) -> bool:
    """True when an exception, or its cause, is an HTTP 401 or 403. The text is never read."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for attr in ("status_code", "status", "http_status"):
            raw = getattr(current, attr, None)
            if isinstance(raw, int) and raw in {401, 403}:
                return True
        response = getattr(current, "response", None)
        if response is not None:
            code = getattr(response, "status_code", None)
            if isinstance(code, int) and code in {401, 403}:
                return True
        if type(current).__name__ in {"AuthenticationError", "PermissionDeniedError"}:
            return True
        current = current.__cause__ or current.__context__
    return False


async def _plan_call(*args: Any, **kwargs: Any) -> Plan:
    """One plan attempt. An auth failure becomes one safe line and drops the provider error."""
    try:
        return await _plan_once(*args, **kwargs)
    except PlanRejected:
        raise
    except Exception as exc:
        if _auth_status(exc):
            raise PlannerAuthError() from None
        raise


def plan_problems(plan: Plan, *, max_tasks: int = 6) -> str | None:
    reasons: list[str] = []
    if len(plan.tasks) > max_tasks:
        reasons.append(f"task count {len(plan.tasks)} exceeds {max_tasks}")
    for task in plan.tasks:
        if not task.allowed_paths:
            reasons.append(f"{task.id} edits no file")
        for raw in [*task.allowed_paths, *task.denied_paths]:
            why = path_violation(raw)
            if why:
                reasons.append(f"{task.id} path {raw} rejected: {why}")
        for label, text in (("title", task.title), ("instructions", task.instructions)):
            stem = forbidden_stem(text)
            if stem:
                reasons.append(f"{task.id} {label} starts with {stem}")
                break
    if not reasons:
        return None
    return "; ".join(reasons)


def assign_checks(plan: Plan, checks: dict[str, str]) -> Plan:
    """Fill empty check_ids from the edited suffix. Install commands are not checks."""
    if not checks:
        return plan
    tasks = []
    for task in plan.tasks:
        if task.check_ids:
            tasks.append(task)
            continue
        paths = " ".join(task.allowed_paths).lower()
        py = [name for name, command in checks.items() if _is_python_check(command)]
        js = [name for name, command in checks.items() if _is_js_check(command)]
        chosen: list[str] = []
        if ".py" in paths:
            chosen.extend(py[:2])
        if any(ext in paths for ext in (".ts", ".tsx", ".js", ".jsx")):
            chosen.extend(js[:1])
        tasks.append(task.model_copy(update={"check_ids": chosen[:5]}))
    return plan.model_copy(update={"tasks": tasks})


def _is_python_check(command: str) -> bool:
    text = command.lower()
    if "pip install" in text:
        return False
    return "pytest" in text or "ruff" in text


def _is_js_check(command: str) -> bool:
    text = command.lower()
    return "npm" in text or "node" in text or "tsc" in text


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


async def _complete(
    llm: Any,
    user_text: str,
    *,
    sink: EventSink | None = None,
    run_id: str = "",
    usage: Any = None,
    agent_name: str = "planner",
    instruction: str = "",
    output_key: str = "plan",
    tool_guard: Any = None,
) -> str:
    """One LlmAgent turn. output_schema stays unset (S1)."""
    from google.adk.agents import LlmAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from treefleet.orchestrator.callbacks import model_hooks, tool_hooks

    before_model = after_model = hooked_before = after_tool = None
    if sink is not None and run_id and usage is not None:
        before_model, after_model = model_hooks(sink, run_id, agent_name, usage)
        hooked_before, after_tool = tool_hooks(sink, run_id)

    def guarded(*_args: object, **kwargs: object) -> dict[str, str] | None:
        raw = kwargs.get("args")
        why = tool_guard(str(kwargs.get("tool_name") or ""), raw if isinstance(raw, dict) else {})
        if why:
            return {"error": str(why)}
        if hooked_before is not None:
            hooked_before(*_args, **kwargs)
        return None

    before_tool = guarded if tool_guard is not None else hooked_before
    agent = LlmAgent(
        name=agent_name,
        model=llm,
        instruction=instruction or _PLAN_INSTRUCTION,
        output_key=output_key,
        before_model_callback=before_model,
        after_model_callback=after_model,
        before_tool_callback=before_tool,
        after_tool_callback=after_tool,
    )
    service = InMemorySessionService()
    await service.create_session(app_name=agent_name, user_id="u", session_id="s")
    runner = Runner(app_name=agent_name, agent=agent, session_service=service)
    message = types.Content(role="user", parts=[types.Part(text=user_text)])
    text = ""
    async for event in runner.run_async(user_id="u", session_id="s", new_message=message):
        content = getattr(event, "content", None)
        if content is None:
            continue
        for part in content.parts or []:
            if getattr(part, "text", None):
                text = part.text
    session = await service.get_session(app_name=agent_name, user_id="u", session_id="s")
    stored = ""
    if session is not None:
        stored = str(session.state.get(output_key) or "")
    return stored or text


def _path_hint(text: str) -> str | None:
    try:
        data = json.loads(_normalize_plan(text))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list):
        return None
    reasons: list[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "task")
        paths = list(task.get("allowed_paths") or []) + list(task.get("denied_paths") or [])
        if not paths:
            reasons.append(f"{task_id} edits no file")
        for raw in paths:
            why = path_violation(str(raw))
            if why:
                reasons.append(f"{task_id} path {raw} rejected: {why}")
    return "; ".join(reasons) if reasons else None


def _emit_plan(sink: EventSink | None, run_id: str, allowed: bool, reason: str) -> None:
    if sink is None or not run_id:
        return
    decision = GuardDecision(
        stage=GuardStage.PLAN,
        allowed=allowed,
        detector="deterministic",
        reasons=[] if allowed else [reason[:200]],
    )
    sink.emit_sync(
        RunEvent(
            run_id=run_id,
            seq=0,
            source="guard",
            type="guard.decision",
            payload=decision.model_dump(mode="json"),
        )
    )


async def _plan_once(
    llm: Any,
    goal: str,
    tree: str,
    checks: dict[str, str] | None,
    *,
    max_tasks: int,
    sink: EventSink | None,
    run_id: str,
    usage: Any,
    escalate: Any = None,
    model_name: str = "",
    escalate_name: str = "",
    reactions: Any = None,
) -> Plan:
    """Parse Plan JSON. One JSON repair, then one re-plan when the guard rejects it."""
    from pydantic import ValidationError

    from treefleet.contracts import RunEvent
    from treefleet.llm_retry import with_llm_retry

    available = checks or {}
    listed = "\n".join(f"{name}: {command}" for name, command in available.items()) or "none"
    prompt = f"Goal:\n{goal}\n\nRepository files:\n{tree}\n\nChecks:\n{listed}\n"
    primary = model_name or str(getattr(llm, "model", "planner") or "planner")
    models = [primary]
    if escalate is not None and escalate_name and escalate_name != primary:
        models.append(escalate_name)

    def _emit_llm(kind: str, payload: dict[str, Any]) -> None:
        if sink is None or not run_id:
            return
        sink.emit_sync(
            RunEvent(
                run_id=run_id,
                seq=0,
                source="orchestrator",
                type=kind,  # type: ignore[arg-type]
                payload=payload,
            )
        )

    async def durable(user_text: str) -> str:
        async def call(chosen: str) -> str:
            agent = llm if chosen == primary else (escalate or llm)
            return await _complete(agent, user_text, sink=sink, run_id=run_id, usage=usage)

        return await with_llm_retry(call, models=models, emit=_emit_llm, agent="planner", reactions=reactions)

    text = await durable(prompt)
    try:
        plan = Plan.model_validate_json(_normalize_plan(text))
    except (ValidationError, json.JSONDecodeError) as exc:
        detail = _path_hint(text) or str(exc)
        repair = (
            f"The plan was rejected: {detail}.\n"
            "Paths must be repo-relative. No .., no absolute paths, no .git, no .cursor, no .env, "
            "no *.pem and no id_rsa. Every task edits at least one file. "
            "complexity is trivial, moderate, or complex. Return only the corrected JSON object.\n\n"
            f"{prompt}"
        )
        text = await durable(repair[:4000])
        try:
            plan = Plan.model_validate_json(_normalize_plan(text))
        except (ValidationError, json.JSONDecodeError) as again:
            raise PlanRejected(f"plan rejected: {_path_hint(text) or again}") from None
    problem = plan_problems(plan, max_tasks=max_tasks)
    if problem is None:
        return assign_checks(plan, available)
    replan = (
        f"The plan was rejected: {problem}. "
        "Every task must edit a file. allowed_paths names that file. "
        "Paths must be repo-relative. No .., no absolute paths, no .git, no .cursor, no .env. "
        "Do not start a title or instructions with inspect, review, explore, investigate, run, or check. "
        "Leave depends_on empty. Return only the corrected JSON object.\n\n"
        f"{prompt}"
    )
    try:
        text = await durable(replan[:8000])
        plan = Plan.model_validate_json(_normalize_plan(text))
    except (ValidationError, json.JSONDecodeError) as exc:
        raise PlanRejected(f"plan rejected: {_path_hint(text) or exc}") from None
    problem = plan_problems(plan, max_tasks=max_tasks)
    if problem:
        raise PlanRejected(f"plan rejected: {problem}")
    return assign_checks(plan, available)


def planner_choice(settings: Settings) -> tuple[str, str]:
    """Flash plans. v4-pro is only the escalation after a rejected repair."""
    return settings.models.fast, settings.models.strong


def scripted_llm(text: str) -> Any:
    """The Phase 5 plan-guard hook: every completion returns the same plan JSON."""
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    class Scripted(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):  # noqa: ANN001
            del llm_request, stream
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))

    return Scripted(model="forced")


async def plan_goal(
    llm: Any,
    goal: str,
    tree: str,
    checks: dict[str, str] | None = None,
    *,
    model_name: str = "",
    escalate: Any = None,
    escalate_name: str = "",
    used: list[str] | None = None,
    max_tasks: int = 6,
    sink: EventSink | None = None,
    run_id: str = "",
    usage: Any = None,
    reactions: Any = None,
) -> Plan:
    """Plan, one repair, then one deepseek-v4-pro retry when the planner was flash."""
    kwargs = {
        "max_tasks": max_tasks,
        "sink": sink,
        "run_id": run_id,
        "usage": usage,
        "escalate": escalate,
        "model_name": model_name,
        "escalate_name": escalate_name or "deepseek/deepseek-v4-pro",
        "reactions": reactions,
    }
    try:
        plan = await _plan_call(llm, goal, tree, checks, **kwargs)
        if used is not None:
            used.append(model_name)
    except PlanRejected as first:
        _emit_plan(sink, run_id, False, str(first))
        if escalate is None or "flash" not in model_name.lower():
            raise
        try:
            plan = await _plan_call(
                escalate,
                goal,
                tree,
                checks,
                max_tasks=max_tasks,
                sink=sink,
                run_id=run_id,
                usage=usage,
                model_name=escalate_name or "deepseek/deepseek-v4-pro",
            )
            if used is not None:
                used.append(escalate_name or "deepseek/deepseek-v4-pro")
        except PlanRejected as second:
            _emit_plan(sink, run_id, False, str(second))
            raise second from None
    _emit_plan(sink, run_id, True, "plan accepted")
    return plan


def _discovered_ids(bundle: RunBundle) -> set[str] | None:
    """None keeps offline tests off the real CLI. An empty set means discovery failed."""
    if bundle.fakes is not None:
        return None
    from treefleet.cursor_models import UnknownModelError, discover

    try:
        found = discover(Path(bundle.settings.cursor.cli), bundle.settings.cursor.list_flag)
    except (UnknownModelError, OSError):
        return set()
    return {item.id for item in found}


def model_selected_payload(decision: RouteDecision) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_id": decision.task_id,
        "model": decision.cursor_model or "",
        "source": decision.source,
        "complexity": decision.complexity,
    }
    if decision.source == "fallback":
        payload["note"] = "fallback to default tier"
    return payload


def _known_or_default(chosen: str | None, known: set[str] | None, default_id: str) -> tuple[str | None, bool]:
    """Keep a discovered id. An unknown id falls back to the default tier when that id is known."""
    if known is None or not chosen or chosen in known:
        return chosen, False
    if default_id and default_id in known:
        return default_id, True
    return None, True


def route_plan(
    plan: Plan,
    request: RunRequest,
    settings: Settings,
    jev: Any = None,
    known: set[str] | None = None,
) -> list[RouteDecision]:
    scored: dict[str, str] = {}
    if jev is not None and request.use_jev and request.worker_model == "router":
        try:
            scored = jev.complexities({task.id: task.instructions for task in plan.tasks})
        except Exception:
            scored = {}
    decisions: list[RouteDecision] = []
    for task in plan.tasks:
        cursor, source = resolve_cursor_model(task.model_override, request, settings)
        complexity = task.complexity
        if source == "router" and task.id in scored:
            complexity = Complexity(scored[task.id])
            cursor = settings.models.router.model_for(complexity.value)
            source = "jev"
        cursor, fell_back = _known_or_default(cursor, known, settings.models.router.fallback())
        if fell_back:
            source = "fallback"
        decisions.append(
            RouteDecision(
                task_id=task.id,
                model=cursor or settings.models.fast,
                complexity=complexity,
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
        pid = proc.interpreter_pid if proc is not None else None
        if worktree.exists():
            owned.add(worktree)
        proof = verify_task_isolation(
            bundle.repo,
            task_id=task_id,
            worktree=worktree,
            run_id=bundle.run_id,
            base_ref=bundle.request.base_ref,
            allowed_paths=list(task.allowed_paths),
            denied_paths=list(task.denied_paths),
            pid=pid,
            reported_cwd=proc.reported_cwd if proc is not None else None,
            ready_at=proc.ready_at if proc is not None else None,
            cwd_required=proc is not None,
            observe_pid=proc.pid if proc is not None and pid is None else None,
        )
        if not proof.process_cwd_matches:
            bundle.sink.emit_sync(
                RunEvent(
                    run_id=bundle.run_id,
                    seq=0,
                    source="orchestrator",
                    type="isolation.proof_detail",
                    task_id=task_id,
                    payload={"attempts": [item.model_dump(mode="json") for item in proof.cwd_attempts]},
                )
            )
        proofs.append(proof)
    main = verify_main(bundle.repo, bundle.main_before or snapshot_main(bundle.repo))
    siblings = verify_siblings(bundle.siblings_before, snapshot_siblings(bundle.repo), owned)
    if not siblings.ok:
        bundle.notes.append("sibling worktree changed: " + ", ".join(siblings.offending))
    return IsolationReport(tasks=proofs, main=main)


def _recorded_guards(path: Path) -> list[GuardDecision]:
    """Policy guard.decision rows from the run log. Tool-argument failures stay out."""
    from pydantic import ValidationError

    from treefleet.events import argument_failure, read_events

    if not path.is_file():
        return []
    rows, _skipped = read_events(path)
    found: list[GuardDecision] = []
    for item in rows:
        if item.get("type") != "guard.decision":
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict) or argument_failure(payload):
            continue
        try:
            found.append(GuardDecision.model_validate(payload))
        except ValidationError:
            continue
    return found


def build_report(
    bundle: RunBundle,
    plan: Plan,
    results: list[WorkerResult],
    isolation: IsolationReport,
    routing: list[RouteDecision],
    wall_seconds: float,
    verdicts: list[ReviewVerdict] | None = None,
    integration: IntegrationReport | None = None,
) -> FinalReport:
    task_seconds = sum(item.duration_s for item in results)
    pre_fleet = 0.0
    fleet_wall = 0.0
    if bundle.fleet_started and bundle.started:
        pre_fleet = max(0.0, bundle.fleet_started - bundle.started)
    if bundle.fleet_ended and bundle.fleet_started:
        fleet_wall = max(0.0, bundle.fleet_ended - bundle.fleet_started)
    notes = list(bundle.request.notes)
    if bundle.request.saboteur_task:
        notes.append(f"DEMO ONLY saboteur: {bundle.request.saboteur_task}"[:300])
    for result in results:
        if result.reverted_generated:
            notes.append(("reverted generated artifacts: " + ", ".join(result.reverted_generated))[:300])
    if results and not any(item.brain_usage_reported for item in results):
        notes.append("worker usage not reported: CrewAI token_usage and the LiteLLM callback were empty"[:300])
    jev = getattr(bundle, "jev", None)
    calls = jev.calls if jev is not None else bundle.jev_calls
    tokens = jev.input_tokens if jev is not None else bundle.jev_tokens
    costs = collect_costs(
        bundle.settings,
        results,
        planner=bundle.planner_usage,
        planner_model=bundle.planner_model or bundle.settings.models.fast,
        jev_calls=calls,
        jev_tokens=tokens,
        reviewer=list(bundle.reviewer_usages),
    )
    return FinalReport(
        run_id=bundle.run_id,
        goal=bundle.request.goal,
        plan=plan,
        results=results,
        verdicts=verdicts or [],
        isolation=isolation,
        integration=integration,
        routing=routing,
        router="jev" if jev is not None else "fallback",
        notes=notes[:20],
        guard_decisions=_recorded_guards(bundle.runs_root / bundle.run_id / "events.jsonl"),
        metrics=assemble_metrics(
            wall=wall_seconds,
            pre_fleet=pre_fleet,
            fleet_wall=fleet_wall,
            task_seconds=task_seconds,
            costs=costs,
            jev_calls=calls,
            task_count=len(results),
            review_seconds=bundle.review_seconds,
            integration_seconds=bundle.integration_seconds,
        ),
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
    forced = os.environ.get("TREEFLEET_FORCE_PLAN")
    if forced and bundle.fakes is None:
        return scripted_llm(forced)
    if bundle.fakes is not None and bundle.fakes.planner is not None:
        return bundle.fakes.planner
    from google.adk.models.lite_llm import LiteLlm

    return LiteLlm(model=planner_choice(bundle.settings)[0])


def _base_agent():
    from google.adk.agents import BaseAgent

    return BaseAgent


BaseAgent = _base_agent()


class RepoScanAgent(BaseAgent):
    bundle: Any

    async def _run_async_impl(self, ctx: Any) -> AsyncGenerator[Any, None]:
        started = time.monotonic()
        text = scan_text(self.bundle.repo)
        _phase(self.bundle, "scan", time.monotonic() - started)
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
        from treefleet.orchestrator.callbacks import UsageBox
        from treefleet.sessions import note_stage, remember_plan

        note_stage(self.bundle, "plan")
        started = time.monotonic()
        tree = str(ctx.session.state.get(StateKey.REPO_MAP, ""))
        fast, pro = planner_choice(self.bundle.settings)
        forced = bool(os.environ.get("TREEFLEET_FORCE_PLAN")) and self.bundle.fakes is None
        model_name = "forced" if forced else fast
        escalate = None
        if not forced and "flash" in model_name.lower() and self.bundle.fakes is None:
            from google.adk.models.lite_llm import LiteLlm

            escalate = LiteLlm(model=pro)
        box = UsageBox()
        used: list[str] = []
        plan = await plan_goal(
            planner_llm(self.bundle),
            self.bundle.request.goal,
            tree,
            dict(self.bundle.request.checks),
            model_name=model_name,
            escalate=escalate,
            escalate_name=pro,
            used=used,
            max_tasks=self.bundle.settings.guards.max_tasks,
            sink=self.bundle.sink,
            run_id=self.bundle.run_id,
            usage=box,
            reactions=self.bundle.settings.reactions,
        )
        self.bundle.planner_model = used[-1] if used else model_name
        self.bundle.planner_usage = box.usage
        approved = True
        if self.bundle.request.plan_approval:
            approved = True if self.bundle.approve is None else bool(self.bundle.approve(plan))
            source = getattr(self.bundle.approve, "source", "http") if self.bundle.approve is not None else "http"
            source = "ui" if source == "ui" else "http"
            self.bundle.sink.emit_sync(
                RunEvent(
                    run_id=self.bundle.run_id,
                    seq=0,
                    source="orchestrator",
                    type="awaiting_approval",
                    payload={"approved": approved},
                )
            )
            if approved:
                self.bundle.sink.emit_sync(
                    RunEvent(
                        run_id=self.bundle.run_id,
                        seq=0,
                        source="orchestrator",
                        type="approval.granted",
                        payload={"source": source, "approved": True},
                    )
                )
        self.bundle.approved = approved
        if approved:
            remember_plan(self.bundle, plan)
        _phase(self.bundle, "planner", time.monotonic() - started)
        yield _adk_event(ctx, self.name, {StateKey.PLAN: dump_plan(plan), "approved": approved})


class RouterAgent(BaseAgent):
    bundle: Any

    async def _run_async_impl(self, ctx: Any) -> AsyncGenerator[Any, None]:
        if not self.bundle.approved:
            return
        started = time.monotonic()
        plan = read_plan(ctx.session.state)
        known = _discovered_ids(self.bundle)
        routing = route_plan(plan, self.bundle.request, self.bundle.settings, getattr(self.bundle, "jev", None), known)
        for decision in routing:
            self.bundle.sink.emit_sync(
                RunEvent(
                    run_id=self.bundle.run_id,
                    seq=0,
                    source="orchestrator",
                    type="model_selected",
                    task_id=decision.task_id,
                    payload=model_selected_payload(decision),
                )
            )
        _phase(self.bundle, "router", time.monotonic() - started)
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
        verdicts = [ReviewVerdict.model_validate(item) for item in ctx.session.state.get(StateKey.VERDICTS, [])]
        raw_integration = ctx.session.state.get(StateKey.INTEGRATION)
        integration = IntegrationReport.model_validate(raw_integration) if raw_integration else None
        wall = max(0.0, time.monotonic() - self.bundle.started)
        report = build_report(
            self.bundle,
            plan,
            list(results.values()),
            isolation,
            routing,
            wall,
            verdicts,
            integration,
        )
        folder = self.bundle.runs_root / self.bundle.run_id
        folder.mkdir(parents=True, exist_ok=True)
        body, _count = redact(report.model_dump_json(indent=2))
        markdown, _count = redact(report.to_markdown())
        (folder / "report.json").write_text(body, encoding="utf-8")
        (folder / "report.md").write_text(markdown, encoding="utf-8")
        from treefleet.timing import baseline_seconds, timing_markdown

        phases = dict(self.bundle.phases)
        phases.setdefault("baseline", baseline_seconds(self.bundle.request.notes))
        phases["review"] = self.bundle.review_seconds
        phases["revise"] = self.bundle.revise_seconds
        phases["integrate"] = self.bundle.integration_seconds
        (folder / "timing.md").write_text(timing_markdown(phases), encoding="utf-8")
        yield _adk_event(ctx, self.name, {StateKey.REPORT: report.model_dump(mode="json")})
