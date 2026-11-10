"""Phase 6: cost rows, reviewer, revise loop, integrator, saboteur flag."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from treefleet.cli import main
from treefleet.config import Pricing, config_path, load_settings
from treefleet.contracts import (
    CheckResult,
    CursorCall,
    FileChange,
    FinalReport,
    IntegrationReport,
    IsolationProof,
    IsolationReport,
    MainCheckoutProof,
    Plan,
    ReviewerUsage,
    ReviewVerdict,
    RunMetrics,
    RunRequest,
    SelfReportedIsolation,
    TaskSpec,
    TokenUsage,
    WorkerResult,
    assemble_metrics,
    isolation_overall,
)
from treefleet.cursor_run import SABOTEUR_LINE, cursor_instruction, demo_break
from treefleet.git.merge import integrate
from treefleet.git.worktrees import GitError, create_worktree, remove_worktree
from treefleet.guards.cost import cost_rows, is_peak, usage_from_object
from treefleet.mcp_servers.gitops_server import READ_ONLY_TOOLS
from treefleet.orchestrator.agents import model_selected_payload, route_plan
from treefleet.orchestrator.review import (
    MAX_REVISIONS,
    apply_rubric,
    approved_branches,
    review_loop,
    review_or_escalate,
    reviewer_tools,
    screen_review_call,
    screen_review_result,
    selected_branches,
    verdict_unparseable,
    verdict_with_repair,
)
from treefleet.timing import timing_markdown

pytestmark = pytest.mark.phase6

SHA = "a" * 40
ROOT = Path(__file__).resolve().parents[1]


def _pricing() -> Pricing:
    return Pricing(
        litellm_url="https://example.invalid",
        jev_usd_per_million_input=0.042,
        jev_source_url="https://example.invalid",
        deepseek_flash_input_per_million=0.15,
        deepseek_flash_cache_hit_per_million=0.003,
        deepseek_flash_output_per_million=0.6,
        deepseek_pro_input_per_million=0.66,
        deepseek_pro_cache_hit_per_million=0.022,
        deepseek_pro_output_per_million=1.98,
        cursor_composer_input_per_million=0.5,
        cursor_composer_cache_read_per_million=0.2,
        cursor_composer_output_per_million=2.5,
        cursor_composer_fast_input_per_million=3,
        cursor_composer_fast_cache_read_per_million=0.5,
        cursor_composer_fast_output_per_million=15,
    )


def _result(task_id: str = "alpha", **update: object) -> WorkerResult:
    branch = f"tf/abc12345/{task_id}"
    base: dict[str, object] = {
        "run_id": "abc12345",
        "task_id": task_id,
        "attempt": 1,
        "status": "done",
        "branch": branch,
        "head_sha": SHA,
        "commit_made": True,
        "model": "deepseek/deepseek-flash",
        "isolation": SelfReportedIsolation(pid=1, cwd="C:/wt", toplevel="C:/wt", branch=branch, mcp_root="C:/wt"),
    }
    base.update(update)
    return WorkerResult(**base)  # type: ignore[arg-type]


def _rows(results: list[WorkerResult], when: datetime) -> list:
    return cost_rows(
        _pricing(),
        results,
        planner=TokenUsage(input_tokens=10, output_tokens=4),
        planner_model="deepseek/deepseek-v4-pro",
        worker_model="deepseek/deepseek-flash",
        jev_calls=1,
        jev_tokens=100,
        when=when,
    )


def test_cost_rows_split_brain_and_cursor() -> None:
    cursor = TokenUsage(input_tokens=11, output_tokens=12, cached_input_tokens=13, cache_write_tokens=14)
    result = _result(
        cursor_model="composer-2.5-fast",
        brain_usage=TokenUsage(input_tokens=100, output_tokens=20),
        brain_usage_reported=True,
        usage=cursor,
        cursor_usage_reported=True,
    )
    when = datetime(2026, 10, 27, 16, tzinfo=UTC)
    lines = _rows([result], when)
    assert [item.component for item in lines] == ["planner", "worker", "cursor", "jev"]
    worker = lines[1]
    assert worker.model == "deepseek/deepseek-flash"
    assert worker.usage.input_tokens == 100
    assert worker.usage.output_tokens == 20
    got = lines[2]
    assert got.model == "composer-2.5-fast"
    assert got.usage.input_tokens == 11
    assert got.usage.output_tokens == 12
    assert got.usage.cached_input_tokens == 13
    assert got.usage.cache_write_tokens == 14
    assert got.label == "estimate"
    assert got.usage.input_tokens != worker.usage.input_tokens


def test_missing_cursor_usage_is_not_the_brain_numbers() -> None:
    brain = TokenUsage(input_tokens=100, output_tokens=20, cached_input_tokens=3, cache_write_tokens=4)
    result = _result(
        cursor_model="composer-2.5-fast",
        brain_usage=brain,
        brain_usage_reported=True,
        usage=brain,
        cursor_usage_reported=False,
    )
    lines = _rows([result], datetime(2026, 10, 27, 16, tzinfo=UTC))
    cursor = next(item for item in lines if item.component == "cursor")
    worker = next(item for item in lines if item.component == "worker")
    assert cursor.label == "usage not reported"
    assert cursor.usage.input_tokens == 0
    assert cursor.usage.output_tokens == 0
    assert cursor.usage.cached_input_tokens == 0
    assert cursor.usage.cache_write_tokens == 0
    assert worker.usage.input_tokens == 100
    assert "flash" in worker.model


def test_single_task_speedup_is_not_applicable() -> None:
    metrics = RunMetrics(
        wall_seconds=10,
        sequential_seconds=9,
        fleet_wall_seconds=7,
        task_seconds=8,
        task_count=1,
    )
    text = metrics.definitions()
    assert "Speedup: n/a (single task)" in text
    assert "Fleet speedup: n/a (single task)" in text


def test_official_rates_match_the_fetched_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")
    pricing = load_settings().pricing
    assert pricing.deepseek_flash_input_per_million == 0.15
    assert pricing.deepseek_flash_cache_hit_per_million == 0.003
    assert pricing.deepseek_flash_output_per_million == 0.6
    assert pricing.deepseek_pro_input_per_million == 0.66
    assert pricing.deepseek_pro_cache_hit_per_million == 0.022
    assert pricing.deepseek_pro_output_per_million == 1.98
    assert pricing.cursor_composer_input_per_million == 0.5
    assert pricing.cursor_composer_cache_read_per_million == 0.2
    assert pricing.cursor_composer_output_per_million == 2.5
    assert pricing.cursor_composer_fast_input_per_million == 3
    assert pricing.cursor_composer_fast_cache_read_per_million == 0.5
    assert pricing.cursor_composer_fast_output_per_million == 15
    text = config_path().read_text(encoding="utf-8")
    assert "https://api-docs.deepseek.com/quick_start/pricing" in text
    assert "https://cursor.com/docs/models" in text
    assert "2026-10-27" in text
    assert "cursor-grok-4.6-high" in text


def test_deepseek_peak_window_doubles_the_off_peak_rate() -> None:
    off = datetime(2026, 10, 27, 16, tzinfo=UTC)
    peak = datetime(2026, 10, 28, 2, tzinfo=UTC)
    weekend = datetime(2026, 10, 31, 2, tzinfo=UTC)
    assert not is_peak(off)
    assert is_peak(peak)
    assert not is_peak(weekend)
    result = _result(
        brain_usage=TokenUsage(input_tokens=1_000_000),
        brain_usage_reported=True,
        cursor_usage_reported=False,
    )
    off_usd = next(item.usd for item in _rows([result], off) if item.component == "worker")
    peak_line = next(item for item in _rows([result], peak) if item.component == "worker")
    assert off_usd == pytest.approx(0.15)
    assert peak_line.usd == pytest.approx(0.30)
    assert peak_line.label == "peak"


async def test_verdict_json_repairs_once() -> None:
    calls: list[str] = []

    async def complete(prompt: str) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return "not json"
        return '{"verdict":"revise","confidence":0.4,"reasons":["broken"],"required_changes":["fix it"]}'

    verdict = await verdict_with_repair(complete, "alpha", "review this")
    assert len(calls) == 2
    assert verdict.verdict == "revise"
    assert verdict.required_changes == ["fix it"]
    assert verdict.reasons == ["broken"]

    async def always_bad(_prompt: str) -> str:
        calls.append("bad")
        return "still not json"

    before = len(calls)
    rejected = await verdict_with_repair(always_bad, "alpha", "review this")
    assert len(calls) - before == 2
    assert rejected.verdict == "reject"


async def test_revise_then_approve_is_attempt_two_on_the_same_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")
    seen: list[tuple[str, int, str]] = []
    proved = 0

    async def review(_task_id: str, _result: WorkerResult, round_no: int) -> ReviewVerdict:
        if round_no == 1:
            return ReviewVerdict(
                task_id="alpha",
                verdict="revise",
                confidence=0.6,
                reasons=["tests failed"],
                required_changes=["fix the test"],
                revision_instructions="fix the test",
            )
        return ReviewVerdict(task_id="alpha", verdict="approve", confidence=0.9, reasons=["ok"])

    async def rerun(_task_id: str, result: WorkerResult, feedback: str, attempt: int) -> WorkerResult:
        seen.append((result.branch, attempt, feedback))
        return result.model_copy(update={"attempt": attempt})

    def prove(_snapshot: dict[str, WorkerResult]) -> None:
        nonlocal proved
        proved += 1

    results, verdicts = await review_loop(
        ["alpha"],
        {"alpha": _result()},
        review=review,
        rerun=rerun,
        prove=prove,
    )
    assert seen == [("tf/abc12345/alpha", 2, "fix the test")]
    assert results["alpha"].attempt == 2
    assert results["alpha"].branch == "tf/abc12345/alpha"
    assert verdicts["alpha"].verdict == "approve"
    assert proved == 1
    loaded = load_settings()
    assert loaded.revise.max_rounds == 1
    assert loaded.revise.round_budget_s == 240
    assert MAX_REVISIONS == loaded.revise.max_rounds


async def test_three_revises_reject_and_exclude() -> None:
    events: list[tuple[str, str, int]] = []
    reruns = 0

    async def review(task_id: str, _result: WorkerResult, _round_no: int) -> ReviewVerdict:
        return ReviewVerdict(
            task_id=task_id,
            verdict="revise",
            confidence=0.5,
            reasons=["still broken"],
            required_changes=["fix the test"],
            revision_instructions="fix the test",
        )

    async def rerun(_task_id: str, result: WorkerResult, _feedback: str, attempt: int) -> WorkerResult:
        nonlocal reruns
        reruns += 1
        return result.model_copy(update={"attempt": attempt})

    def emit(kind: str, task_id: str, round_no: int, _payload: dict[str, object]) -> None:
        events.append((kind, task_id, round_no))

    results, verdicts = await review_loop(
        ["alpha"],
        {"alpha": _result()},
        review=review,
        rerun=rerun,
        emit=emit,
    )
    assert reruns == 1
    assert results["alpha"].attempt == 2
    assert verdicts["alpha"].verdict == "reject"
    assert "still broken" in verdicts["alpha"].reasons
    assert "alpha" not in approved_branches(results, verdicts)
    assert ("review_started", "alpha", 1) in events
    assert ("review_verdict", "alpha", 1) in events
    assert ("revise_started", "alpha", 1) in events
    assert ("review_started", "alpha", 2) in events
    assert not any(kind == "revise_started" and round_no == 2 for kind, _task, round_no in events)


def test_reviewer_tools_are_read_only() -> None:
    assert set(reviewer_tools()) == set(READ_ONLY_TOOLS)
    for name in ("merge", "commit", "worktree_add", "cleanup", "push"):
        assert screen_review_call(name) == "mutating tool"
    assert screen_review_call("show_file", {"path": "../secret"}) == "parent"
    assert screen_review_call("diff", {"ref": "main"}) is None
    secret = "sk-" + "abcdefghijklmnopqrstuvwxyz"
    assert secret not in screen_review_result(f"diff token {secret}")


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        shell=False,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr.strip() or proc.stdout.strip() or "git failed")
    return proc.stdout


def _short_root() -> Path:
    path = Path(r"C:\tf") if sys.platform == "win32" else Path("/tmp/tf")
    path.mkdir(parents=True, exist_ok=True)
    return path


async def test_empty_worker_skips_review() -> None:
    seen: list[str] = []

    async def review(task_id: str, result: WorkerResult, round_no: int) -> ReviewVerdict:
        seen.append(task_id)
        return ReviewVerdict(task_id=task_id, verdict="approve", confidence=1, reasons=["unused"])

    async def rerun(task_id: str, result: WorkerResult, feedback: str, attempt: int) -> WorkerResult:
        raise AssertionError(task_id)

    failed = _result(status="failed", commit_made=False, head_sha=None, error="worker made no changes: shim edit")
    _updated, verdicts = await review_loop(["alpha"], {"alpha": failed}, review=review, rerun=rerun)
    assert seen == []
    assert verdicts["alpha"].verdict == "reject"
    assert verdicts["alpha"].reasons == ["worker made no changes: shim edit"]


def test_downstream_commit_stays_off_the_integrated_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "treefleet@local"], repo)
    _git(["config", "user.name", "TreeFleet"], repo)
    (repo / "README.md").write_text("base\n", encoding="utf-8", newline="\n")
    _git(["add", "README.md"], repo)
    _git(["commit", "-m", "init"], repo)
    run_id = uuid.uuid4().hex[:8]
    root = _short_root()
    made: list[Path] = []
    try:
        upstream = create_worktree(repo, run_id, "alpha", root, "main")
        made.append(upstream)
        (upstream / "README.md").write_text("base\nupstream\n", encoding="utf-8", newline="\n")
        _git(["add", "README.md"], upstream)
        _git(["commit", "-m", "tf alpha"], upstream)
        downstream = create_worktree(repo, run_id, "beta", root, "main")
        made.append(downstream)
        (downstream / "DOWN.md").write_text("downstream\n", encoding="utf-8", newline="\n")
        _git(["add", "DOWN.md"], downstream)
        _git(["commit", "-m", "tf beta"], downstream)
        plan = Plan.model_validate(
            {
                "goal": "Two tasks.",
                "rationale": "Beta follows alpha.",
                "tasks": [
                    {
                        "id": "alpha",
                        "title": "Upstream",
                        "instructions": "Edit the readme with one line.",
                        "allowed_paths": ["README.md"],
                    },
                    {
                        "id": "beta",
                        "title": "Downstream",
                        "instructions": "Add DOWN.md with one line.",
                        "allowed_paths": ["DOWN.md"],
                        "depends_on": ["alpha"],
                    },
                ],
            }
        )
        results = {
            "alpha": _result("alpha", branch=f"tf/{run_id}/alpha", run_id=run_id),
            "beta": _result("beta", branch=f"tf/{run_id}/beta", run_id=run_id),
        }
        verdicts = {
            "alpha": ReviewVerdict(task_id="alpha", verdict="reject", confidence=1, reasons=["bad diff"]),
            "beta": ReviewVerdict(task_id="beta", verdict="approve", confidence=1, reasons=["ok"]),
        }
        branches, reasons = selected_branches(plan, results, verdicts)
        assert "beta" not in branches
        assert reasons["beta"] == "upstream alpha rejected"
        report = integrate(repo, run_id, ["alpha", "beta"], branches, "main", worktree_root=root, reasons=reasons)
        integrated = root / run_id / "integrated"
        assert "beta" not in report.merged
        assert not (integrated / "DOWN.md").exists()
        assert "tf beta" not in _git(["log", "--oneline"], integrated)
    finally:
        made.append(root / run_id / "integrated")
        for path in reversed(made):
            try:
                remove_worktree(repo, path)
            except GitError:
                pass


def test_conflict_is_reported_and_a_later_task_still_merges(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-b", "main"], repo)
    _git(["config", "user.email", "treefleet@local"], repo)
    _git(["config", "user.name", "TreeFleet"], repo)
    _git(["config", "core.autocrlf", "false"], repo)
    (repo / "README.md").write_text("alpha\nmiddle\nbeta\n", encoding="utf-8", newline="\n")
    (repo / "notes.md").write_text("notes\n", encoding="utf-8", newline="\n")
    _git(["add", "README.md", "notes.md"], repo)
    _git(["commit", "-m", "init"], repo)
    run_id = uuid.uuid4().hex[:8]
    root = _short_root()
    made: list[Path] = []
    try:
        for task, rel, text in (
            ("alpha", "README.md", "ALPHA\nmiddle\nbeta\n"),
            ("beta", "README.md", "CHANGED\nmiddle\nbeta\n"),
            ("gamma", "notes.md", "GAMMA\n"),
        ):
            worktree = create_worktree(repo, run_id, task, root, "main")
            made.append(worktree)
            (worktree / rel).write_text(text, encoding="utf-8", newline="\n")
            _git(["add", rel], worktree)
            _git(["commit", "-m", task], worktree)
        main_head = _git(["rev-parse", "HEAD"], repo).strip()
        report = integrate(
            repo,
            run_id,
            ["alpha", "beta", "gamma"],
            {
                "alpha": f"tf/{run_id}/alpha",
                "beta": f"tf/{run_id}/beta",
                "gamma": f"tf/{run_id}/gamma",
            },
            "main",
            worktree_root=root,
            checks={"status": "git status --porcelain"},
            reasons={"later": "rejected: not in this run"},
        )
        assert report.merged == ["alpha", "gamma"]
        assert report.conflicts and report.conflicts[0].path == "README.md"
        assert any(line.startswith("beta:") and "conflict" in line for line in report.excluded)
        assert report.branch == f"tf/{run_id}/integrated"
        assert _git(["rev-parse", "HEAD"], repo).strip() == main_head
        assert "<<<<<<<" not in _git(["show", "HEAD:README.md"], repo)
        integrated = root / run_id / "integrated"
        assert "GAMMA" in (integrated / "notes.md").read_text(encoding="utf-8")
        assert "<<<<<<<" not in (integrated / "README.md").read_text(encoding="utf-8")
        assert report.checks and report.checks[0].passed
        assert report.merge_command == f"git -C {repo} merge {report.branch}"
        assert _git(["rev-parse", "--abbrev-ref", "HEAD"], repo).strip() == "main"
    finally:
        made.append(root / run_id / "integrated")
        for path in reversed(made):
            try:
                remove_worktree(repo, path)
            except GitError:
                pass
        shutil.rmtree(root / run_id, ignore_errors=True)


def test_final_report_has_verdict_rounds_and_the_merge_command() -> None:
    task = TaskSpec(
        id="alpha",
        title="Add tests",
        instructions="Add unit tests for the notifications module.",
        allowed_paths=["tests/test_notifications.py"],
    )
    plan = Plan(goal="Add the missing notification tests", rationale="coverage", tasks=[task])
    proof = IsolationProof(
        task_id="alpha",
        worktree_path="C:/wt/alpha",
        branch="tf/abc12345/alpha",
        process_cwd_matches=True,
        toplevel_matches=True,
        registered_in_worktree_list=True,
        canary_absent=True,
        changes_within_allowed_paths=True,
    )
    main = MainCheckoutProof(
        status_before=[],
        status_after=[],
        head_before=SHA,
        head_after=SHA,
        canary_present=True,
    )
    report = FinalReport(
        run_id="abc12345",
        goal=plan.goal,
        plan=plan,
        results=[_result(attempt=3)],
        verdicts=[ReviewVerdict(task_id="alpha", verdict="approve", confidence=0.9, reasons=["ok"])],
        isolation=IsolationReport(tasks=[proof], main=main),
        integration=IntegrationReport(
            branch="tf/abc12345/integrated",
            head_sha=SHA,
            merged=["alpha"],
            excluded=["beta: rejected: checks failed"],
            user_branch="demo-base",
            merge_command="git -C D:/repo merge tf/abc12345/integrated",
            checks=[CheckResult(name="pytest", command="pytest -q", exit_code=0, passed=True)],
        ),
        metrics=RunMetrics(wall_seconds=10, sequential_seconds=25, task_count=1),
        notes=["DEMO ONLY saboteur: beta"],
    )
    text = report.to_markdown()
    assert "| Task | Status | Verdict | Revise rounds | Branch | Files | Checks |" in text
    assert "| alpha | done | approve | 2 |" in text
    assert "Branch: `tf/abc12345/integrated`" in text
    assert "beta: rejected: checks failed" in text
    assert "git -C D:/repo merge tf/abc12345/integrated" in text
    assert "Post-merge checks: pytest:pass" in text
    assert "DEMO ONLY saboteur: beta" in text
    assert "Speedup: n/a (single task)" in text


def test_saboteur_matches_the_task_id_or_a_planner_prefix() -> None:
    from treefleet.orchestrator.fleet import task_is_saboteur

    assert task_is_saboteur(None, "notifications-tests") is False
    assert task_is_saboteur("notifications-tests", "notifications-tests") is True
    assert task_is_saboteur("notifications-tests", "add-notifications-tests") is True
    assert task_is_saboteur("notifications-tests", "admin-tests") is False


def test_saboteur_is_off_unless_the_task_is_named() -> None:
    request = RunRequest(goal="Add unit tests for the notifications module.", repo="D:/repo")
    assert request.saboteur_task is None
    assert request.scenario == "custom"
    assert cursor_instruction("edit the tests") == "edit the tests"
    text = cursor_instruction("edit the tests", saboteur=True, revision="fix the assertion")
    assert SABOTEUR_LINE in text
    assert "DEMO ONLY" in text
    assert "break an existing test" in text
    assert "Required changes:" in text
    assert "fix the assertion" in text


def test_demo_break_fails_an_existing_test_once(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    target = tests / "test_auth.py"
    target.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    demo_break(tmp_path)
    demo_break(tmp_path)
    text = target.read_text(encoding="utf-8")
    assert text.count("test_treefleet_saboteur_demo") == 1
    assert "DEMO ONLY saboteur" in text


def test_sequential_time_adds_review_and_integration() -> None:
    metrics = assemble_metrics(
        wall=100,
        pre_fleet=10,
        fleet_wall=20,
        task_seconds=30,
        review_seconds=4,
        integration_seconds=6,
        task_count=2,
    )
    assert metrics.sequential_seconds == pytest.approx(50)
    assert metrics.wall_seconds == 100
    text = metrics.definitions()
    assert "Review time: 4.0s" in text
    assert "Integration time: 6.0s" in text


def test_reviewer_rows_and_every_cursor_call_sum_to_the_total() -> None:
    calls = [
        CursorCall(
            round=1, model="composer-2.5", usage=TokenUsage(input_tokens=1000, output_tokens=100), reported=True
        ),
        CursorCall(round=2, model="composer-2.5", usage=TokenUsage(input_tokens=2000, output_tokens=50), reported=True),
    ]
    result = _result(cursor_model="composer-2.5", cursor_calls=calls, brain_usage_reported=False)
    reviewer = [
        ReviewerUsage(
            task_id="alpha",
            round=1,
            model="deepseek/deepseek-flash",
            usage=TokenUsage(input_tokens=10, output_tokens=5),
            reported=True,
        )
    ]
    lines = cost_rows(
        _pricing(),
        [result],
        planner=TokenUsage(input_tokens=10, output_tokens=4),
        planner_model="deepseek/deepseek-v4-pro",
        worker_model="deepseek/deepseek-flash",
        jev_calls=0,
        jev_tokens=0,
        when=datetime(2026, 10, 31, 16, tzinfo=UTC),
        reviewer=reviewer,
    )
    cursor = [item for item in lines if item.component == "cursor"]
    assert len(cursor) == 2
    assert "alpha r1" in cursor[0].label and "alpha r2" in cursor[1].label
    assert any(item.component == "reviewer" and "flash" in item.model for item in lines)
    assert RunMetrics(wall_seconds=1, sequential_seconds=1, costs=lines).total_usd == round(
        sum(c.usd for c in lines), 6
    )


def test_empty_crew_usage_is_not_reported() -> None:
    assert usage_from_object(None) is None
    empty = usage_from_object({"prompt_tokens": 0, "completion_tokens": 0})
    assert empty is None
    found = usage_from_object({"prompt_tokens": 8, "completion_tokens": 3, "cached_prompt_tokens": 1})
    assert found is not None
    assert found.input_tokens == 8
    assert found.output_tokens == 3
    assert found.cached_input_tokens == 1


def test_timing_breakdown_lists_each_stage() -> None:
    text = timing_markdown(
        {"scan": 0.1, "baseline": 2.2, "planner": 3, "fleet": 4, "review": 5, "revise": 6, "integrate": 7}
    )
    for name in ("scan", "baseline", "planner", "fleet", "review", "revise", "integrate"):
        assert f"- {name}:" in text


async def test_flash_reviewer_escalates_when_json_stays_invalid() -> None:
    seen: list[str] = []

    async def complete_for(model: str, _text: str) -> str:
        seen.append(model)
        if "flash" in model:
            return "not json"
        return '{"verdict": "approve", "confidence": 0.9, "reasons": ["ok"]}'

    verdict, used = await review_or_escalate(
        complete_for,
        ["deepseek/deepseek-flash", "deepseek/deepseek-v4-pro"],
        "alpha",
        "review",
    )
    assert used == "deepseek/deepseek-v4-pro"
    assert verdict.verdict == "approve"
    assert seen[0].endswith("flash")
    assert verdict_unparseable(
        ReviewVerdict(task_id="alpha", verdict="reject", confidence=0, reasons=["verdict JSON was not valid: x"])
    )


async def test_identical_diff_stops_the_revise_loop() -> None:
    reruns = 0

    async def review(task_id: str, _result: WorkerResult, _round_no: int) -> ReviewVerdict:
        return ReviewVerdict(
            task_id=task_id,
            verdict="revise",
            confidence=0.4,
            reasons=["again"],
            required_changes=["fix the test"],
        )

    async def rerun(_task_id: str, result: WorkerResult, _feedback: str, attempt: int) -> WorkerResult:
        nonlocal reruns
        reruns += 1
        return result.model_copy(update={"attempt": attempt})

    _results, verdicts = await review_loop(
        ["alpha"],
        {"alpha": _result()},
        review=review,
        rerun=rerun,
        diff_of=lambda _result: "same diff",
    )
    assert reruns == 1
    assert verdicts["alpha"].verdict == "reject"
    assert verdicts["alpha"].reasons == ["no new diff"]


def _two_task_plan() -> Plan:
    return Plan.model_validate(
        {
            "goal": "Two tasks.",
            "rationale": "Beta follows alpha.",
            "tasks": [
                {
                    "id": "alpha",
                    "title": "Upstream",
                    "instructions": "Edit the readme with one line.",
                    "allowed_paths": ["README.md"],
                },
                {
                    "id": "beta",
                    "title": "Downstream",
                    "instructions": "Add DOWN.md with one line.",
                    "allowed_paths": ["DOWN.md"],
                    "depends_on": ["alpha"],
                },
            ],
        }
    )


async def test_revise_budget_records_the_reason() -> None:
    async def review(task_id: str, _result: WorkerResult, _round_no: int) -> ReviewVerdict:
        return ReviewVerdict(
            task_id=task_id,
            verdict="revise",
            confidence=0.4,
            reasons=["again"],
            required_changes=["fix the test"],
        )

    async def rerun(_task_id: str, result: WorkerResult, _feedback: str, _attempt: int) -> WorkerResult:
        await asyncio.sleep(1)
        return result

    files = [FileChange(path="tests/test_admin.py", additions=94, deletions=0)]
    results, verdicts = await review_loop(
        ["alpha"],
        {"alpha": _result(files_changed=files)},
        review=review,
        rerun=rerun,
        round_budget_s=0.05,
        diff_of=lambda _result: "same diff",
    )
    assert results["alpha"].status == "needs_review"
    assert verdicts["alpha"].reasons == ["revise round exceeded 0.05s"]


async def test_unchanged_in_scope_diff_needs_review() -> None:
    files = [FileChange(path="tests/test_admin.py", additions=94, deletions=0)]

    async def review(task_id: str, _result: WorkerResult, _round_no: int) -> ReviewVerdict:
        return ReviewVerdict(
            task_id=task_id,
            verdict="revise",
            confidence=0.4,
            reasons=["again"],
            required_changes=["fix the test"],
        )

    async def rerun(_task_id: str, result: WorkerResult, _feedback: str, attempt: int) -> WorkerResult:
        return result.model_copy(update={"attempt": attempt, "cursor_message": "on disk (94 lines)."})

    results, verdicts = await review_loop(
        ["alpha"],
        {"alpha": _result(files_changed=files)},
        review=review,
        rerun=rerun,
        diff_of=lambda _result: "same diff",
    )
    assert results["alpha"].status == "needs_review"
    assert results["alpha"].head_sha
    assert "94 lines" in results["alpha"].cursor_message
    assert verdicts["alpha"].reasons[0] == "no new diff"
    plan = Plan(
        goal="Add the missing admin tests now",
        rationale="coverage",
        tasks=[
            TaskSpec(
                id="alpha",
                title="Admin tests",
                instructions="Add unit tests for the admin module.",
                allowed_paths=["tests/test_admin.py"],
            )
        ],
    )
    branches, reasons = selected_branches(plan, results, verdicts)
    assert branches == {}
    assert reasons["alpha"].startswith("needs review")
    included, _skipped = selected_branches(plan, results, verdicts, include_needs_review=True)
    assert included["alpha"] == results["alpha"].branch
    blocked, why = selected_branches(
        _two_task_plan(),
        {"alpha": results["alpha"], "beta": _result("beta")},
        {
            "alpha": verdicts["alpha"],
            "beta": ReviewVerdict(task_id="beta", verdict="approve", confidence=1, reasons=["ok"]),
        },
        include_needs_review=True,
    )
    assert "beta" not in blocked
    assert why["beta"] == "upstream alpha needs review"
    text = FinalReport(
        run_id="abc12345",
        goal=plan.goal,
        plan=plan,
        results=[results["alpha"]],
        verdicts=[verdicts["alpha"]],
        isolation=IsolationReport(
            tasks=[],
            main=MainCheckoutProof(
                status_before=[], status_after=[], head_before=SHA, head_after=SHA, canary_present=True
            ),
        ),
        integration=IntegrationReport(repo=r"D:\pr-sentinel-demo", branch="tf/abc12345/integrated", head_sha=SHA),
        metrics=RunMetrics(wall_seconds=1, sequential_seconds=1, task_count=1),
    ).to_markdown()
    assert "needs review on `tf/abc12345/alpha`" in text
    assert r"git -C D:\pr-sentinel-demo merge tf/abc12345/alpha" in text


def test_weak_but_valid_tests_are_approved_with_suggestions() -> None:
    source = "def test_send_email():\n    assert send_email('hi') == 'sent'\n"
    asked = ReviewVerdict(
        task_id="alpha",
        verdict="revise",
        confidence=0.6,
        reasons=["The assertion could be stronger."],
        required_changes=["Also assert the logger call."],
    )
    approved = apply_rubric(
        asked,
        checks_failed=False,
        outside_scope=False,
        empty_diff=False,
        test_text=source,
        functions=["send_email"],
    )
    assert approved.verdict == "approve"
    assert "logger" in " ".join(approved.suggestions)
    assert approved.required_changes == []
    silent = apply_rubric(
        asked,
        checks_failed=False,
        outside_scope=False,
        empty_diff=False,
        test_text="def test_send_email():\n    send_email('hi')\n",
        functions=["send_email"],
    )
    assert silent.verdict == "revise"
    assert "tests assert nothing" in silent.reasons
    missed = apply_rubric(
        asked,
        checks_failed=False,
        outside_scope=False,
        empty_diff=False,
        test_text=source,
        functions=["send_email", "send_sms"],
    )
    assert missed.verdict == "revise"
    assert any("send_sms" in item for item in missed.reasons)


async def test_no_new_diff_keeps_the_cursor_message() -> None:
    seen: list[dict[str, object]] = []

    async def review(task_id: str, _result: WorkerResult, _round_no: int) -> ReviewVerdict:
        return ReviewVerdict(
            task_id=task_id,
            verdict="revise",
            confidence=0.4,
            reasons=["again"],
            required_changes=["fix the test"],
        )

    async def rerun(_task_id: str, result: WorkerResult, _feedback: str, attempt: int) -> WorkerResult:
        return result.model_copy(
            update={"attempt": attempt, "cursor_message": "tests/test_admin.py is on disk (94 lines)."}
        )

    def emit(_kind: str, _task_id: str, _round_no: int, payload: dict[str, object]) -> None:
        seen.append(payload)

    _results, verdicts = await review_loop(
        ["alpha"],
        {"alpha": _result()},
        review=review,
        rerun=rerun,
        diff_of=lambda _result: "same diff",
        emit=emit,
    )
    assert verdicts["alpha"].reasons[0] == "no new diff"
    assert "94 lines" in verdicts["alpha"].reasons[1]
    assert seen[-1]["cursor_message"] == "tests/test_admin.py is on disk (94 lines)."
    report = FinalReport(
        run_id="abc12345",
        goal="Add the missing notification tests",
        plan=Plan(
            goal="Add the missing notification tests",
            rationale="coverage",
            tasks=[
                TaskSpec(
                    id="alpha",
                    title="Add tests",
                    instructions="Add unit tests for the notifications module.",
                    allowed_paths=["tests/test_notifications.py"],
                )
            ],
        ),
        results=[_result(cursor_message="tests/test_admin.py is on disk (94 lines).")],
        verdicts=[verdicts["alpha"]],
        isolation=IsolationReport(
            tasks=[
                IsolationProof(
                    task_id="alpha",
                    worktree_path="C:/wt/alpha",
                    branch="tf/abc12345/alpha",
                    process_cwd_matches=True,
                    toplevel_matches=True,
                    registered_in_worktree_list=True,
                    canary_absent=True,
                    changes_within_allowed_paths=True,
                )
            ],
            main=MainCheckoutProof(
                status_before=[],
                status_after=[],
                head_before=SHA,
                head_after=SHA,
                canary_present=True,
            ),
        ),
        metrics=RunMetrics(wall_seconds=1, sequential_seconds=1, task_count=1),
    )
    assert "94 lines" in report.to_markdown()


def test_unknown_cursor_id_falls_back_to_the_default_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")
    settings = load_settings()
    plan = Plan(
        goal="Add one sentence to README.md about pull requests.",
        rationale="The readme is the only file this task should touch.",
        tasks=[
            TaskSpec(
                id="alpha",
                title="Edit readme",
                instructions="Add one sentence to README.md about pull requests.",
                allowed_paths=["README.md"],
                model_override="not-a-real-model",
            )
        ],
    )
    request = RunRequest(goal=plan.goal, repo="D:/repo")
    known = {"composer-2.5", "composer-2.5-fast"}
    decision = route_plan(plan, request, settings, known=known)[0]
    assert decision.cursor_model == settings.models.router.fallback()
    assert decision.cursor_model in known
    assert decision.source == "fallback"
    assert model_selected_payload(decision)["note"] == "fallback to default tier"


def _proof(**update: object) -> IsolationProof:
    base: dict[str, object] = {
        "task_id": "alpha",
        "worktree_path": "C:/wt/alpha",
        "branch": "tf/abc12345/alpha",
        "process_cwd_matches": True,
        "toplevel_matches": True,
        "registered_in_worktree_list": True,
        "canary_absent": True,
        "changes_within_allowed_paths": True,
    }
    base.update(update)
    return IsolationProof(**base)  # type: ignore[arg-type]


def _main(**update: object) -> MainCheckoutProof:
    base: dict[str, object] = {
        "status_before": ["?? .treefleet-canary"],
        "status_after": ["?? .treefleet-canary"],
        "head_before": SHA,
        "head_after": SHA,
        "canary_present": True,
    }
    base.update(update)
    return MainCheckoutProof(**base)  # type: ignore[arg-type]


def test_contained_scope_violation_is_labeled_and_four_failures_stay_fail() -> None:
    clean = IsolationReport(tasks=[_proof()], main=_main())
    reverted = _result(reverted_scope=["tests/test_auth.py"])
    assert isolation_overall(clean, [reverted]) == "PASS (1 contained violation)"
    report = FinalReport(
        run_id="abc12345",
        goal="Add one sentence to README.md about pull requests.",
        plan=Plan(
            goal="Add one sentence to README.md about pull requests.",
            rationale="One file.",
            tasks=[
                TaskSpec(
                    id="alpha",
                    title="Edit readme",
                    instructions="Add one sentence to README.md about pull requests.",
                    allowed_paths=["README.md"],
                )
            ],
        ),
        results=[reverted],
        verdicts=[],
        isolation=clean,
        metrics=RunMetrics(wall_seconds=1, sequential_seconds=1, task_count=1),
    )
    assert "NO (reverted)" in report.to_markdown()
    assert isolation_overall(IsolationReport(tasks=[_proof()], main=_main(head_after="b" * 40)), []) == "FAIL"
    assert isolation_overall(IsolationReport(tasks=[_proof()], main=_main(canary_present=False)), []) == "FAIL"
    assert (
        isolation_overall(IsolationReport(tasks=[_proof(registered_in_worktree_list=False)], main=_main()), [])
        == "FAIL"
    )
    assert (
        isolation_overall(IsolationReport(tasks=[_proof(changes_within_allowed_paths=False)], main=_main()), [])
        == "FAIL"
    )


def test_cli_help_labels_saboteur_as_demo(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "DEMO ONLY" in capsys.readouterr().out
