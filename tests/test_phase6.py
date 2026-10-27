"""Phase 6: cost rows, reviewer, revise loop, integrator, saboteur flag."""

from __future__ import annotations

import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from treefleet.cli import main
from treefleet.config import Pricing, load_settings
from treefleet.contracts import (
    CheckResult,
    FinalReport,
    IntegrationReport,
    IsolationProof,
    IsolationReport,
    MainCheckoutProof,
    Plan,
    ReviewVerdict,
    RunMetrics,
    RunRequest,
    SelfReportedIsolation,
    TaskSpec,
    TokenUsage,
    WorkerResult,
)
from treefleet.cursor_run import SABOTEUR_LINE, cursor_instruction, demo_break
from treefleet.git.merge import integrate
from treefleet.git.worktrees import GitError, create_worktree, remove_worktree
from treefleet.guards.cost import cost_rows, is_peak
from treefleet.mcp_servers.gitops_server import READ_ONLY_TOOLS
from treefleet.orchestrator.review import (
    MAX_REVISIONS,
    approved_branches,
    review_loop,
    reviewer_tools,
    screen_review_call,
    screen_review_result,
    verdict_with_repair,
)

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
    pricing = load_settings(ROOT / "treefleet.toml").pricing
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
    text = (ROOT / "treefleet.toml").read_text(encoding="utf-8")
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


async def test_revise_then_approve_is_attempt_two_on_the_same_branch() -> None:
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
    assert MAX_REVISIONS == 2


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
    assert reruns == 2
    assert results["alpha"].attempt == 3
    assert verdicts["alpha"].verdict == "reject"
    assert "still broken" in verdicts["alpha"].reasons
    assert "alpha" not in approved_branches(results, verdicts)
    assert ("review_started", "alpha", 1) in events
    assert ("review_verdict", "alpha", 1) in events
    assert ("revise_started", "alpha", 1) in events
    assert ("review_started", "alpha", 3) in events
    assert not any(kind == "revise_started" and round_no == 3 for kind, _task, round_no in events)


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


def test_cli_help_labels_saboteur_as_demo(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "DEMO ONLY" in capsys.readouterr().out
