"""Phase 14.4: heuristic routing when Jev scores are absent."""

from __future__ import annotations

import pytest

from treefleet.config import load_settings
from treefleet.contracts import Complexity, FinalReport, Plan, RouteDecision, RunRequest, TaskSpec, from_wire, to_wire
from treefleet.orchestrator.agents import (
    _COMPLEXITY_RANK,
    _TIER_RANK,
    heuristic_tier,
    route_plan,
    routing_source_counts,
)

pytestmark = pytest.mark.phase11


def _plan(*rows: tuple[str, Complexity]) -> Plan:
    tasks = [
        TaskSpec(
            id=task_id,
            title=f"Task {task_id}",
            instructions=f"Edit README.md for {task_id}.",
            allowed_paths=["README.md"],
            complexity=complexity,
        )
        for task_id, complexity in rows
    ]
    return Plan(goal="Edit README.md for each task.", rationale="Disjoint edits.", tasks=tasks)


def _settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    return load_settings()


def test_heuristic_tier_mapping_table() -> None:
    assert heuristic_tier(Complexity.TRIVIAL) == "simple"
    assert heuristic_tier("simple") == "simple"
    assert heuristic_tier(Complexity.MODERATE) == "moderate"
    assert heuristic_tier(Complexity.COMPLEX) == "complex"
    assert heuristic_tier(None) is None
    assert heuristic_tier("") is None
    assert heuristic_tier("not-a-band") is None


def test_heuristic_never_picks_above_planner_complexity() -> None:
    for name in ("trivial", "simple", "moderate", "complex"):
        tier = heuristic_tier(name)
        assert tier is not None
        assert _TIER_RANK[tier] <= _COMPLEXITY_RANK[name]


def test_no_jev_three_complexities_three_tiers(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    plan = _plan(
        ("task-trivial", Complexity.TRIVIAL),
        ("task-moderate", Complexity.MODERATE),
        ("task-complex", Complexity.COMPLEX),
    )
    request = RunRequest(goal=plan.goal, repo="D:/repo", worker_model="router")
    decisions = route_plan(plan, request, settings, jev=None)
    by_id = {item.task_id: item for item in decisions}
    assert by_id["task-trivial"].source == "heuristic"
    assert by_id["task-moderate"].source == "heuristic"
    assert by_id["task-complex"].source == "heuristic"
    assert by_id["task-trivial"].cursor_model == settings.models.router.simple
    assert by_id["task-moderate"].cursor_model == settings.models.router.moderate
    assert by_id["task-complex"].cursor_model == settings.models.router.complex
    assert len({item.cursor_model for item in decisions}) == 3


def test_jev_error_uses_heuristic(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")
    settings = load_settings()

    class Boom:
        def complexities(self, _tasks: dict[str, str]) -> dict[str, str]:
            raise RuntimeError("unreachable")

    plan = _plan(("alpha", Complexity.TRIVIAL), ("beta", Complexity.COMPLEX))
    request = RunRequest(goal=plan.goal, repo="D:/repo", worker_model="router", use_jev=True)
    decisions = route_plan(plan, request, settings, jev=Boom())
    assert {item.source for item in decisions} == {"heuristic"}
    assert decisions[0].cursor_model == settings.models.router.simple
    assert decisions[1].cursor_model == settings.models.router.complex


def test_jev_scores_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")
    settings = load_settings()

    class Scored:
        def complexities(self, tasks: dict[str, str]) -> dict[str, str]:
            return {task_id: "complex" for task_id in tasks}

    plan = _plan(("alpha", Complexity.TRIVIAL))
    request = RunRequest(goal=plan.goal, repo="D:/repo", worker_model="router", use_jev=True)
    decision = route_plan(plan, request, settings, jev=Scored())[0]
    assert decision.source == "jev"
    assert decision.complexity == Complexity.COMPLEX
    assert decision.cursor_model == settings.models.router.complex


def test_partial_scores_mix_jev_and_heuristic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")
    settings = load_settings()

    class Partial:
        def complexities(self, tasks: dict[str, str]) -> dict[str, str]:
            return {"scored": "trivial"}

    plan = _plan(("scored", Complexity.COMPLEX), ("missing", Complexity.COMPLEX))
    request = RunRequest(goal=plan.goal, repo="D:/repo", worker_model="router", use_jev=True)
    by_id = {item.task_id: item for item in route_plan(plan, request, settings, jev=Partial())}
    assert by_id["scored"].source == "jev"
    assert by_id["scored"].cursor_model == settings.models.router.simple
    assert by_id["missing"].source == "heuristic"
    assert by_id["missing"].cursor_model == settings.models.router.complex


def test_unknown_complexity_uses_default_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    assert heuristic_tier("mystery-band") is None
    plan = _plan(("alpha", Complexity.MODERATE))
    request = RunRequest(goal=plan.goal, repo="D:/repo", worker_model="router")
    monkeypatch.setattr("treefleet.orchestrator.agents.heuristic_tier", lambda _c: None)
    decision = route_plan(plan, request, settings, jev=None)[0]
    assert decision.source == "router"
    assert decision.cursor_model == settings.models.router.fallback()


def test_routing_sources_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")
    settings = load_settings()

    class Partial:
        def complexities(self, tasks: dict[str, str]) -> dict[str, str]:
            return {"alpha": "moderate"}

    plan = _plan(("alpha", Complexity.TRIVIAL), ("bravo", Complexity.COMPLEX))
    request = RunRequest(goal=plan.goal, repo="D:/repo", worker_model="router", use_jev=True)
    routing = route_plan(plan, request, settings, jev=Partial())
    counts = routing_source_counts(routing)
    assert counts == {"jev": 1, "heuristic": 1, "router": 0}


def test_old_report_loads_without_routing_sources() -> None:
    from treefleet.contracts import (
        CostLine,
        IsolationProof,
        IsolationReport,
        MainCheckoutProof,
        RunMetrics,
        SelfReportedIsolation,
        TokenUsage,
        WorkerResult,
    )

    plan = _plan(("aa", Complexity.MODERATE))
    proof = IsolationProof(
        task_id="aa",
        worktree_path="C:/wt/aa",
        branch="tf/abc123/aa",
        process_cwd_matches=True,
        toplevel_matches=True,
        registered_in_worktree_list=True,
        canary_absent=True,
        changes_within_allowed_paths=True,
    )
    main = MainCheckoutProof(
        status_before=["?? .treefleet-canary"],
        status_after=["?? .treefleet-canary"],
        head_before="a" * 40,
        head_after="a" * 40,
        canary_present=True,
    )
    result = WorkerResult(
        run_id="abc123",
        task_id="aa",
        attempt=1,
        status="done",
        branch="tf/abc123/aa",
        head_sha="a" * 40,
        commit_made=True,
        model="deepseek/deepseek-flash",
        isolation=SelfReportedIsolation(
            pid=10,
            cwd="C:/wt/aa",
            toplevel="C:/wt/aa",
            branch="tf/abc123/aa",
            mcp_root="C:/wt/aa",
        ),
    )
    fresh = FinalReport(
        run_id="abc123",
        goal=plan.goal,
        plan=plan,
        results=[result],
        verdicts=[],
        isolation=IsolationReport(tasks=[proof], main=main),
        routing=[
            RouteDecision(
                task_id="aa",
                model="deepseek/deepseek-flash",
                complexity=Complexity.MODERATE,
                confidence=1,
                source="router",
                cursor_model="composer-2.5",
            )
        ],
        metrics=RunMetrics(
            wall_seconds=1,
            sequential_seconds=1,
            costs=[CostLine(component="worker", model="m", usage=TokenUsage(input_tokens=1), usd=0)],
        ),
        routing_sources={"heuristic": 1},
    )
    payload = fresh.model_dump(mode="json")
    payload.pop("routing_sources", None)
    payload.pop("router", None)
    report = FinalReport.model_validate(payload)
    assert report.routing_sources == {}
    assert report.router == "fallback"
    assert from_wire(to_wire(report), FinalReport).routing_sources == {}


def test_heuristic_event_payload_ids_and_tiers_only(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    plan = _plan(("alpha", Complexity.TRIVIAL), ("beta", Complexity.MODERATE))
    request = RunRequest(goal=plan.goal, repo="D:/repo", worker_model="router")
    routing = route_plan(plan, request, settings, jev=None)
    rows = [{"id": d.task_id, "tier": heuristic_tier(d.complexity)} for d in routing if d.source == "heuristic"]
    assert rows == [
        {"id": "alpha", "tier": "simple"},
        {"id": "beta", "tier": "moderate"},
    ]
    blob = str(rows)
    assert "Edit README" not in blob
    assert "instructions" not in blob
