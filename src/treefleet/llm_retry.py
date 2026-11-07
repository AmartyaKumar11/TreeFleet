"""Transient LLM provider errors: classify, redact, retry, escalate. Shared by orch and worker."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

from treefleet.guards.text import redact

T = TypeVar("T")

_BACKOFF_S = (2.0, 6.0)
_RETRY_ATTEMPTS = 2  # after the first try: two more = three flash attempts, then escalate once


class ModelUnavailableError(RuntimeError):
    """Provider still down after retries (and optional escalation)."""

    def __init__(self, error_class: str, detail: str = "") -> None:
        self.error_class = error_class
        self.detail = detail
        super().__init__(f"{error_class}: {detail}"[:300] if detail else error_class)


def _litellm_exc_types() -> tuple[type[BaseException], ...]:
    try:
        from litellm import exceptions as litellm_exc
    except Exception:
        return ()
    found: list[type[BaseException]] = []
    for name in ("InternalServerError", "ServiceUnavailableError", "RateLimitError", "APIConnectionError"):
        item = getattr(litellm_exc, name, None)
        if isinstance(item, type) and issubclass(item, BaseException):
            found.append(item)
    return tuple(found)


def _status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "status", "http_status"):
        raw = getattr(exc, attr, None)
        if isinstance(raw, int):
            return raw
    response = getattr(exc, "response", None)
    if response is not None:
        code = getattr(response, "status_code", None)
        if isinstance(code, int):
            return code
    return None


def is_transient_provider_error(exc: BaseException) -> bool:
    """HTTP 5xx, 429, timeouts, connection resets, and named LiteLLM classes."""
    if isinstance(exc, (TimeoutError, ConnectionError, ConnectionResetError, BrokenPipeError)):
        return True
    if isinstance(exc, OSError):
        text = str(exc).lower()
        if "connection reset" in text or "timed out" in text or "broken pipe" in text:
            return True
    code = _status_code(exc)
    if code is not None and (code == 429 or code >= 500):
        return True
    if isinstance(exc, _litellm_exc_types()):
        return True
    name = type(exc).__name__
    if name in {
        "InternalServerError",
        "ServiceUnavailableError",
        "RateLimitError",
        "APIConnectionError",
        "Timeout",
        "ReadTimeout",
        "ConnectTimeout",
    }:
        return True
    lowered = str(exc).lower()
    if "internal server error" in lowered or "rate limit" in lowered:
        return True
    return False


def redact_error(exc: BaseException) -> str:
    """Error class plus a short redacted body. Never log secrets."""
    raw = f"{type(exc).__name__}: {exc}"
    cleaned, _count = redact(raw)
    return cleaned[:300]


def error_class_name(exc: BaseException) -> str:
    return type(exc).__name__


EmitFn = Callable[[str, dict[str, Any]], None]


async def with_llm_retry(
    call: Callable[[str], Awaitable[T]],
    *,
    models: Sequence[str],
    emit: EmitFn | None = None,
    agent: str = "",
    task_id: str | None = None,
    backoff: Sequence[float] = _BACKOFF_S,
) -> T:
    """Up to 2 retries on the first model (2s then 6s), then one call on the escalate model if given."""
    if not models:
        raise ValueError("models must not be empty")
    primary = models[0]
    escalate = models[1] if len(models) > 1 else None
    last: BaseException | None = None
    sequence: list[tuple[str, float | None]] = [(primary, None)]
    for wait in backoff[:_RETRY_ATTEMPTS]:
        sequence.append((primary, wait))
    if escalate is not None and escalate != primary:
        sequence.append((escalate, None))

    for index, (model, wait) in enumerate(sequence):
        if wait is not None:
            await asyncio.sleep(wait)
        try:
            return await call(model)
        except BaseException as exc:
            last = exc
            if not is_transient_provider_error(exc):
                raise
            payload = {
                "agent": agent,
                "task_id": task_id,
                "model": model,
                "attempt": index + 1,
                "error_class": error_class_name(exc),
                "error": redact_error(exc),
                "next": sequence[index + 1][0] if index + 1 < len(sequence) else None,
            }
            if emit is not None:
                emit("llm_retry", {k: v for k, v in payload.items() if v is not None})
            continue
    assert last is not None
    if emit is not None:
        emit(
            "llm_unavailable",
            {
                "agent": agent,
                "task_id": task_id,
                "error_class": error_class_name(last),
                "error": redact_error(last),
            },
        )
    raise ModelUnavailableError(error_class_name(last), redact_error(last)) from last


def with_llm_retry_sync(
    call: Callable[[str], T],
    *,
    models: Sequence[str],
    emit: EmitFn | None = None,
    agent: str = "",
    task_id: str | None = None,
    backoff: Sequence[float] = _BACKOFF_S,
) -> T:
    """Sync twin for CrewAI worker-brain calls."""
    if not models:
        raise ValueError("models must not be empty")
    primary = models[0]
    escalate = models[1] if len(models) > 1 else None
    last: BaseException | None = None
    sequence: list[tuple[str, float | None]] = [(primary, None)]
    for wait in backoff[:_RETRY_ATTEMPTS]:
        sequence.append((primary, wait))
    if escalate is not None and escalate != primary:
        sequence.append((escalate, None))

    for index, (model, wait) in enumerate(sequence):
        if wait is not None:
            time.sleep(wait)
        try:
            return call(model)
        except BaseException as exc:
            last = exc
            if not is_transient_provider_error(exc):
                raise
            payload = {
                "agent": agent,
                "task_id": task_id,
                "model": model,
                "attempt": index + 1,
                "error_class": error_class_name(exc),
                "error": redact_error(exc),
                "next": sequence[index + 1][0] if index + 1 < len(sequence) else None,
            }
            if emit is not None:
                emit("llm_retry", {k: v for k, v in payload.items() if v is not None})
            continue
    assert last is not None
    if emit is not None:
        emit(
            "llm_unavailable",
            {
                "agent": agent,
                "task_id": task_id,
                "error_class": error_class_name(last),
                "error": redact_error(last),
            },
        )
    raise ModelUnavailableError(error_class_name(last), redact_error(last)) from last
