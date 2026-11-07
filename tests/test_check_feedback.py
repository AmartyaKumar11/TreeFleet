"""Failed checks become revision_feedback before the reviewer runs."""

from __future__ import annotations

import pytest

from treefleet.contracts import CheckResult, ReviewVerdict, SelfReportedIsolation, WorkerResult
from treefleet.guards.text import redact
from treefleet.orchestrator.review import check_failure_feedback, review_loop

pytestmark = pytest.mark.phase6

SHA = "a" * 40


def _result(**update: object) -> WorkerResult:
    base: dict[str, object] = {
        "run_id": "abc12345",
        "task_id": "alpha",
        "attempt": 1,
        "status": "done",
        "branch": "tf/abc12345/alpha",
        "head_sha": SHA,
        "commit_made": True,
        "model": "deepseek/deepseek-flash",
        "isolation": SelfReportedIsolation(
            pid=1, cwd="C:/wt", toplevel="C:/wt", branch="tf/abc12345/alpha", mcp_root="C:/wt"
        ),
    }
    base.update(update)
    return WorkerResult(**base)  # type: ignore[arg-type]


def test_check_failure_feedback_names_the_check_and_redacts() -> None:
    checks = [
        CheckResult(
            name="pytest",
            command="pytest -q",
            exit_code=1,
            passed=False,
            stdout_tail="FAILED secret sk-abcdefghij leak\n",
            stderr_tail="",
        )
    ]
    text = check_failure_feedback(checks)
    assert "pytest" in text
    assert "sk-abcdefghij" not in text
    assert "[redacted]" in text or "sk-" not in text
    cleaned, _ = redact(text)
    assert "sk-abcdefghij" not in cleaned
    assert len(text) <= 2000


@pytest.mark.asyncio
async def test_failed_checks_revise_before_reviewer() -> None:
    failed = _result(
        checks=[CheckResult(name="pytest", command="pytest -q", exit_code=1, passed=False, stdout_tail="boom\n")]
    )
    fixed = failed.model_copy(
        update={
            "attempt": 2,
            "checks": [CheckResult(name="pytest", command="pytest -q", exit_code=0, passed=True)],
        }
    )
    seen: list[str] = []

    async def review(task_id: str, result: WorkerResult, round_no: int) -> ReviewVerdict:
        seen.append(f"review:{result.attempt}:{all(c.passed for c in result.checks)}")
        return ReviewVerdict(task_id=task_id, verdict="approve", confidence=1, reasons=["ok"])  # type: ignore[arg-type]

    async def rerun(task_id: str, result: WorkerResult, feedback: str, attempt: int) -> WorkerResult:
        seen.append(f"rerun:{attempt}")
        assert "pytest" in feedback
        assert "boom" in feedback
        return fixed.model_copy(update={"attempt": attempt})

    results, verdicts = await review_loop(
        ["alpha"],
        {"alpha": failed},
        review=review,
        rerun=rerun,
    )
    assert seen[0].startswith("rerun:")
    assert any(item.startswith("review:") for item in seen)
    assert seen.index(next(item for item in seen if item.startswith("rerun:"))) < seen.index(
        next(item for item in seen if item.startswith("review:"))
    )
    assert verdicts["alpha"].verdict == "approve"
    assert results["alpha"].attempt == 2
