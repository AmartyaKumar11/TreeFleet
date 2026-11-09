"""Documentation diffs are not empty tests. The second review is round 2."""

from __future__ import annotations

import pytest

from treefleet.contracts import ReviewVerdict, SelfReportedIsolation, WorkerResult
from treefleet.orchestrator.review import apply_rubric, review_loop, verdict_reason

pytestmark = pytest.mark.phase11

SHA = "a" * 40


def _asked() -> ReviewVerdict:
    return ReviewVerdict(
        task_id="alpha",
        verdict="revise",
        confidence=0.4,
        reasons=["tests assert nothing"],
        required_changes=["tests assert nothing"],
        revision_instructions="tests assert nothing",
    )


def _rubric(text: str) -> ReviewVerdict:
    return apply_rubric(
        _asked(),
        checks_failed=False,
        outside_scope=False,
        empty_diff=False,
        test_text=text,
        functions=[],
    )


def test_readme_and_contributing_diffs_are_not_empty_tests() -> None:
    readme = "diff --git a/README.md b/README.md\n+Pull requests are welcome.\n"
    contributing = "diff --git a/CONTRIBUTING.md b/CONTRIBUTING.md\n+# Contributing\n+Please run the test suite.\n"
    for text in (readme, contributing):
        verdict = _rubric(text)
        assert verdict.verdict == "approve"
        assert "tests assert nothing" not in verdict.reasons


def test_a_test_file_with_no_assert_still_revises() -> None:
    text = "diff --git a/tests/test_notifications.py b/tests/test_notifications.py\n+def test_send():\n+    send()\n"
    verdict = _rubric(text)
    assert verdict.verdict == "revise"
    assert verdict.reasons == ["tests assert nothing"]
    assert verdict_reason(verdict) == "tests assert nothing"


def _result() -> WorkerResult:
    return WorkerResult(
        run_id="abc12345",
        task_id="alpha",
        attempt=1,
        status="done",
        branch="tf/abc12345/alpha",
        head_sha=SHA,
        commit_made=True,
        model="deepseek/deepseek-flash",
        isolation=SelfReportedIsolation(
            pid=1, cwd="C:/wt", toplevel="C:/wt", branch="tf/abc12345/alpha", mcp_root="C:/wt"
        ),
    )


async def test_revise_feedback_is_stored_and_the_second_review_is_round_2() -> None:
    seen: list[tuple[str, int, dict[str, object]]] = []

    async def review(task_id: str, _result: WorkerResult, _round_no: int) -> ReviewVerdict:
        return ReviewVerdict(
            task_id=task_id,
            verdict="revise",
            confidence=0.4,
            reasons=["again"],
            required_changes=["fix the test"],
        )

    async def rerun(_task_id: str, result: WorkerResult, _feedback: str, attempt: int) -> WorkerResult:
        return result.model_copy(update={"attempt": attempt, "cursor_message": "already there"})

    def emit(kind: str, _task_id: str, round_no: int, payload: dict[str, object]) -> None:
        seen.append((kind, round_no, payload))

    await review_loop(
        ["alpha"],
        {"alpha": _result()},
        review=review,
        rerun=rerun,
        diff_of=lambda _result: "same diff",
        emit=emit,
    )
    started = [item for item in seen if item[0] == "revise_started"]
    assert started[0][2]["feedback"] == "fix the test"
    assert len(started[0][2]["feedback"]) <= 600
    rejected = [item for item in seen if item[0] == "review_verdict" and item[2].get("verdict") == "reject"]
    assert rejected[0][1] == 2
