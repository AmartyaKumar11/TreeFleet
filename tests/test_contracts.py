from typing import Any

import pytest
from pydantic import ValidationError

from treefleet.contracts import (
    CheckResult,
    CostLine,
    FinalReport,
    IntegrationConflict,
    IntegrationReport,
    IsolationProof,
    IsolationReport,
    Limits,
    MainCheckoutProof,
    Plan,
    ReviewVerdict,
    RunMetrics,
    RunRequest,
    SelfReportedIsolation,
    TaskSpec,
    TokenUsage,
    WorkerResult,
    WorkerTaskEnvelope,
    from_wire,
    to_wire,
)

pytestmark = pytest.mark.phase1

SHA = "a" * 40
LONG = "Add a Testing section to the README and keep every other section as it is."


def task(tid: str, deps: list[str] | None = None, paths: list[str] | None = None) -> TaskSpec:
    return TaskSpec(
        id=tid, title="Task title", instructions=LONG, allowed_paths=paths or ["README.md"], depends_on=deps or []
    )


def iso() -> SelfReportedIsolation:
    return SelfReportedIsolation(pid=10, cwd="/wt/a", toplevel="/wt/a", branch="tf/abc123/a", mcp_root="/wt/a")


@pytest.mark.parametrize("bad", ["../secrets", "/etc/passwd", "C:\\Windows", "src/../../x", ".git/config", ".git"])
def test_path_patterns_reject_escapes(bad: str) -> None:
    with pytest.raises(ValidationError):
        task("a", paths=[bad])


def test_plan_rejects_cycle_duplicate_and_unknown() -> None:
    goal = "Improve the repository documentation"
    with pytest.raises(ValidationError):
        Plan(goal=goal, rationale="r", tasks=[task("aa", ["bb"]), task("bb", ["aa"])])
    with pytest.raises(ValidationError):
        Plan(goal=goal, rationale="r", tasks=[task("aa"), task("aa")])
    with pytest.raises(ValidationError):
        Plan(goal=goal, rationale="r", tasks=[task("aa", ["zz"])])
    with pytest.raises(ValidationError):
        task("aa", ["aa"])


def test_plan_waves() -> None:
    plan = Plan(
        goal="Improve the repository documentation",
        rationale="r",
        tasks=[task("aa"), task("bb"), task("cc", ["aa", "bb"]), task("dd", ["cc"])],
    )
    assert plan.waves() == [["aa", "bb"], ["cc"], ["dd"]]


def test_worker_result_consistency() -> None:
    base: dict[str, Any] = dict(
        run_id="abc123", task_id="aa", attempt=1, branch="tf/abc123/aa", model="m", isolation=iso()
    )
    with pytest.raises(ValidationError):
        WorkerResult(status="done", **base)
    with pytest.raises(ValidationError):
        WorkerResult(status="failed", **base)
    ok = WorkerResult(status="done", commit_made=True, head_sha=SHA, **base)
    assert from_wire(to_wire(ok), WorkerResult) == ok


def test_check_result_consistency() -> None:
    with pytest.raises(ValidationError):
        CheckResult(name="test", command="pytest", exit_code=1, passed=True)


def test_review_revise_requires_instructions() -> None:
    with pytest.raises(ValidationError):
        ReviewVerdict(task_id="aa", verdict="revise", confidence=0.8)
    ReviewVerdict(task_id="aa", verdict="revise", confidence=0.8, revision_instructions="Fix the failing test.")


def test_extra_fields_rejected_and_envelope_roundtrip() -> None:
    env = WorkerTaskEnvelope(run_id="abc123", task=task("aa"), model="deepseek/deepseek-chat")
    assert from_wire(to_wire(env), WorkerTaskEnvelope) == env
    with pytest.raises(ValidationError):
        WorkerTaskEnvelope.model_validate({**to_wire(env), "surprise": 1})


def test_computed_fields_survive_roundtrip() -> None:
    proof = IsolationProof(
        task_id="aa",
        worktree_path="/wt/a",
        branch="tf/abc123/aa",
        process_cwd_matches=True,
        toplevel_matches=True,
        registered_in_worktree_list=True,
        canary_absent=True,
        changes_within_allowed_paths=True,
    )
    assert proof.ok
    assert from_wire(to_wire(proof), IsolationProof) == proof
    bad = proof.model_copy(update={"canary_absent": False})
    assert not bad.ok


def test_worker_model_default_and_rejects_bad_ids() -> None:
    goal = "A sufficiently long goal"
    request = RunRequest(goal=goal, repo="x")
    assert request.worker_model == "router"
    for bad in ("a b", "x;rm -rf", "a" * 101):
        with pytest.raises(ValidationError):
            RunRequest(goal=goal, repo="x", worker_model=bad)


def test_model_override_and_cursor_model_roundtrip() -> None:
    spec = task("aa").model_copy(update={"model_override": "gpt-5"})
    assert from_wire(to_wire(spec), TaskSpec).model_override == "gpt-5"
    env = WorkerTaskEnvelope(run_id="abc123", task=spec, model="deepseek/deepseek-flash", cursor_model="gpt-5")
    restored = from_wire(to_wire(env), WorkerTaskEnvelope)
    assert restored.cursor_model == "gpt-5"
    assert restored.model == "deepseek/deepseek-flash"


def test_run_request_limits() -> None:
    with pytest.raises(ValidationError):
        RunRequest(goal="short", repo="x")
    with pytest.raises(ValidationError):
        RunRequest(goal="A sufficiently long goal", repo="x", base_ref="main; rm -rf /")
    with pytest.raises(ValidationError):
        RunRequest(goal="A sufficiently long goal", repo="x", max_workers=9)
    assert Limits().timeout_s == 180


def test_final_report_markdown_and_metrics() -> None:
    plan = Plan(goal="Improve the repository documentation", rationale="r", tasks=[task("aa")])
    res = WorkerResult(
        run_id="abc123",
        task_id="aa",
        attempt=1,
        status="done",
        branch="tf/abc123/aa",
        head_sha=SHA,
        commit_made=True,
        model="m",
        isolation=iso(),
    )
    proof = IsolationProof(
        task_id="aa",
        worktree_path="/wt/a",
        branch="tf/abc123/aa",
        process_cwd_matches=True,
        toplevel_matches=True,
        registered_in_worktree_list=True,
        canary_absent=True,
        changes_within_allowed_paths=True,
    )
    main = MainCheckoutProof(
        status_before=["?? .treefleet_canary"],
        status_after=["?? .treefleet_canary"],
        head_before=SHA,
        head_after=SHA,
        canary_present=True,
    )
    metrics = RunMetrics(
        wall_seconds=10,
        sequential_seconds=25,
        costs=[CostLine(component="worker", model="m", usage=TokenUsage(input_tokens=10), usd=0.0123, task_id="aa")],
    )
    report = FinalReport(
        run_id="abc123",
        goal=plan.goal,
        plan=plan,
        results=[res],
        verdicts=[ReviewVerdict(task_id="aa", verdict="approve", confidence=0.9)],
        isolation=IsolationReport(tasks=[proof], main=main),
        integration=IntegrationReport(
            merged=["aa"], conflicts=[IntegrationConflict(path="README.md", between=["aa", "bb"])]
        ),
        metrics=metrics,
    )
    md = report.to_markdown()
    assert "Overall:** PASS" in md and "Speedup: 2.5x" in md and "Conflict in `README.md`" in md
    assert metrics.total_usd == 0.0123
    assert from_wire(to_wire(report), FinalReport) == report
