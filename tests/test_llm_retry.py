"""Provider errors retry, escalate, and never become reject."""

from __future__ import annotations

from typing import Any

import pytest

from treefleet.contracts import ReviewVerdict, SelfReportedIsolation, WorkerResult
from treefleet.llm_retry import (
    ModelUnavailableError,
    is_transient_provider_error,
    redact_error,
    with_llm_retry,
)
from treefleet.orchestrator.review import review_loop

pytestmark = pytest.mark.phase6

SHA = "a" * 40


class _HttpErr(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def test_classifies_transient_error_classes(monkeypatch: pytest.MonkeyPatch) -> None:
    class InternalServerError(Exception):
        pass

    class ServiceUnavailableError(Exception):
        pass

    class RateLimitError(Exception):
        pass

    class APIConnectionError(Exception):
        pass

    fake = type(
        "exc",
        (),
        {
            "InternalServerError": InternalServerError,
            "ServiceUnavailableError": ServiceUnavailableError,
            "RateLimitError": RateLimitError,
            "APIConnectionError": APIConnectionError,
        },
    )
    monkeypatch.setitem(__import__("sys").modules, "litellm.exceptions", fake)  # type: ignore[arg-type]
    monkeypatch.setattr(
        "treefleet.llm_retry._litellm_exc_types",
        lambda: (InternalServerError, ServiceUnavailableError, RateLimitError, APIConnectionError),
    )

    assert is_transient_provider_error(InternalServerError("boom"))
    assert is_transient_provider_error(ServiceUnavailableError("down"))
    assert is_transient_provider_error(RateLimitError("slow"))
    assert is_transient_provider_error(APIConnectionError("reset"))
    assert is_transient_provider_error(TimeoutError("wait"))
    assert is_transient_provider_error(ConnectionResetError("rst"))
    assert is_transient_provider_error(OSError(104, "Connection reset by peer"))
    assert not is_transient_provider_error(ValueError("bad json"))
    assert not is_transient_provider_error(RuntimeError("other"))


def test_classifies_http_status_on_exception() -> None:
    assert is_transient_provider_error(_HttpErr("provider failed", 503))
    assert is_transient_provider_error(_HttpErr("slow", 429))
    assert not is_transient_provider_error(_HttpErr("bad", 400))


def test_redacts_error_bodies() -> None:
    text = redact_error(RuntimeError("secret sk-abcdefghij leak and token=abc"))
    assert "sk-abcdefghij" not in text
    assert "InternalServerError" not in text or "RuntimeError" in text
    assert len(text) <= 300


@pytest.mark.asyncio
async def test_with_llm_retry_backoff_then_escalate(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("treefleet.llm_retry.asyncio.sleep", fake_sleep)
    events: list[tuple[str, dict[str, Any]]] = []
    calls: list[str] = []

    async def call(model: str) -> str:
        calls.append(model)
        if len(calls) < 4:
            raise _HttpErr("provider 503", 503)
        return f"ok:{model}"

    text = await with_llm_retry(
        call,
        models=["flash", "pro"],
        emit=lambda kind, payload: events.append((kind, payload)),
        agent="reviewer",
        task_id="alpha",
    )
    assert text == "ok:pro"
    assert calls == ["flash", "flash", "flash", "pro"]
    assert sleeps == [2.0, 6.0]
    assert any(kind == "llm_retry" for kind, _ in events)


@pytest.mark.asyncio
async def test_with_llm_retry_raises_model_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("treefleet.llm_retry.asyncio.sleep", fake_sleep)
    events: list[str] = []

    async def call(model: str) -> str:
        raise _HttpErr("still down", 500)

    with pytest.raises(ModelUnavailableError) as raised:
        await with_llm_retry(
            call,
            models=["flash", "pro"],
            emit=lambda kind, payload: events.append(kind),
            agent="planner",
        )
    assert raised.value.error_class
    assert "llm_unavailable" in events


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


@pytest.mark.asyncio
async def test_reviewer_unavailable_becomes_needs_review_not_reject() -> None:
    seen: list[str] = []

    async def review(task_id: str, result: WorkerResult, round_no: int) -> ReviewVerdict:
        raise ModelUnavailableError("InternalServerError", "provider 500")

    async def rerun(task_id: str, result: WorkerResult, feedback: str, attempt: int) -> WorkerResult:
        raise AssertionError("should not revise")

    def emit(kind: str, task_id: str, round_no: int, payload: dict[str, Any]) -> None:
        seen.append(kind)
        if kind == "review_verdict":
            seen.append(str(payload.get("verdict")))
            seen.append(str(payload.get("reason") or ""))

    results, verdicts = await review_loop(
        ["alpha"],
        {"alpha": _result()},
        review=review,
        rerun=rerun,
        emit=emit,
    )
    assert results["alpha"].status == "needs_review"
    assert "reviewer unavailable: InternalServerError" in (results["alpha"].error or "")
    assert verdicts["alpha"].verdict != "reject"
    assert any("reviewer unavailable" in item for item in verdicts["alpha"].reasons)
