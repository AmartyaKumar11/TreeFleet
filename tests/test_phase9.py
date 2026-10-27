"""Phase 9: offline eval harness. Live scenarios are not part of the gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from treefleet.contracts import (
    CheckResult,
    CostLine,
    FinalReport,
    IntegrationReport,
    IsolationProof,
    IsolationReport,
    MainCheckoutProof,
    Plan,
    ReviewVerdict,
    RunMetrics,
    SelfReportedIsolation,
    TaskSpec,
    TokenUsage,
    WorkerResult,
)
from treefleet.evals.harness import harness_status, score
from treefleet.evals.report import estimates, render_markdown
from treefleet.evals.scenarios import load_scenarios

pytestmark = pytest.mark.phase9

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def _report(ok: bool) -> FinalReport:
    proof = IsolationProof(
        task_id="alpha",
        worktree_path="C:/wt/alpha",
        branch="tf/abc12345/alpha",
        process_cwd_matches=True,
        toplevel_matches=True,
        registered_in_worktree_list=True,
        canary_absent=True,
        changes_within_allowed_paths=ok,
    )
    main = MainCheckoutProof(
        status_before=["?? .treefleet-canary"],
        status_after=["?? .treefleet-canary"],
        head_before=SHA,
        head_after=SHA,
        canary_present=True,
    )
    result = WorkerResult(
        run_id="abc12345",
        task_id="alpha",
        attempt=1,
        status="done",
        branch="tf/abc12345/alpha",
        head_sha=SHA,
        commit_made=True,
        model="deepseek/deepseek-flash",
        isolation=SelfReportedIsolation(pid=1, cwd="C:/wt", toplevel="C:/wt", branch="b", mcp_root="C:/wt"),
    )
    costs = [
        CostLine(component="planner", model="m", usage=TokenUsage(input_tokens=10), usd=0.01),
        CostLine(component="worker", model="m", usage=TokenUsage(input_tokens=4), usd=0.02),
    ]
    metrics = RunMetrics(wall_seconds=10, sequential_seconds=30, task_seconds=20, costs=costs, task_count=1)
    return FinalReport(
        run_id="abc12345",
        goal="Add one sentence to README.md about pull requests.",
        plan=Plan(
            goal="Add one sentence to README.md about pull requests.",
            rationale="One file is enough for this scenario.",
            tasks=[
                TaskSpec(
                    id="alpha",
                    title="Edit readme",
                    instructions="Add one sentence to README.md about pull requests.",
                    allowed_paths=["README.md"],
                )
            ],
        ),
        results=[result],
        verdicts=[ReviewVerdict(task_id="alpha", verdict="approve", confidence=1)],
        integration=IntegrationReport(
            merged=["alpha"],
            checks=[CheckResult(name="pytest", command="pytest", exit_code=0, passed=True)],
        ),
        isolation=IsolationReport(tasks=[proof], main=main),
        metrics=metrics,
    )


def test_scenarios_file_lists_e1_through_e6() -> None:
    found = load_scenarios(ROOT / "eval" / "scenarios.toml")
    assert [item.id for item in found] == ["E1", "E2", "E3", "E4", "E5", "E6"]
    assert found[1].repeat == 2
    assert [item.name for item in found[5].variant] == ["auto", "router"]


def test_offline_report_is_schema_valid_and_cost_matches_the_rows() -> None:
    scenarios = load_scenarios(ROOT / "eval" / "scenarios.toml")
    report = _report(ok=True)
    assert report.metrics.total_usd == round(sum(item.usd for item in report.metrics.costs), 6)
    row = score(scenarios[0], report, "")
    row["label"] = "E1#1"
    assert row["expectation"] == "pass"
    text = render_markdown([row], cap=3)
    assert "E1" in text
    assert "extrapolation" in "\n".join(estimates([row], 3)).lower()


def test_isolation_violation_makes_the_harness_fail() -> None:
    scenarios = load_scenarios(ROOT / "eval" / "scenarios.toml")
    row = score(scenarios[0], _report(ok=False), "")
    assert row["isolation_violation"] is True
    assert harness_status([row]) == 1
    assert harness_status([{**row, "isolation_violation": False}]) == 0
