"""Optional Langfuse tracing. Off unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are both set.

Every function here is non-fatal: a missing key, a bad key, an unreachable host, an import error or a flush error
never changes the outcome of a run. Nothing from langfuse, openinference, google.adk or crewai is imported at module
level, so importing this module can never fail or slow startup. Only the exception class name is ever logged.
"""

from __future__ import annotations

import contextlib
import logging
import os
import threading
from collections.abc import Iterator
from typing import Any

log = logging.getLogger("treefleet.observability")

TRACE_NAME = "treefleet-run"
CURSOR_SPAN_NAME = "cursor-agent"
FLUSH_TIMEOUT_S = 5.0

_lock = threading.Lock()
_client: Any = None
_role = ""
_instrumented: set[str] = set()


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def enabled() -> bool:
    """True once a Langfuse client was created in this process."""
    return _client is not None


def init_tracing(role: str, *, tracer_provider: Any = None) -> bool:
    """Create the Langfuse client once per process. Returns False (a no-op) when keys are absent or setup fails."""
    global _client, _role
    if _client is not None:
        return True
    public, secret = _env("LANGFUSE_PUBLIC_KEY"), _env("LANGFUSE_SECRET_KEY")
    if not public or not secret:
        return False
    with _lock:
        if _client is not None:
            return True
        try:
            from langfuse import Langfuse

            kwargs: dict[str, Any] = {"public_key": public, "secret_key": secret}
            base = _env("LANGFUSE_BASE_URL") or _env("LANGFUSE_HOST")
            if base:
                # Passed explicitly: SDK versions read LANGFUSE_HOST or LANGFUSE_BASE_URL differently.
                kwargs["base_url"] = base
            if tracer_provider is not None:
                kwargs["tracer_provider"] = tracer_provider
            _client = Langfuse(**kwargs)
            _role = role
        except Exception as exc:
            _client = None
            log.warning("langfuse tracing disabled: %s", type(exc).__name__)
            return False
    return True


def _instrument(key: str, module: str, cls: str) -> bool:
    if key in _instrumented:
        return True
    try:
        import importlib

        getattr(importlib.import_module(module), cls)().instrument()
    except Exception as exc:
        log.warning("%s instrumentation skipped: %s", key, type(exc).__name__)
        return False
    _instrumented.add(key)
    return True


def instrument_orchestrator() -> bool:
    """Google ADK spans. A no-op when tracing is off."""
    if _client is None:
        return False
    return _instrument("adk", "openinference.instrumentation.google_adk", "GoogleADKInstrumentor")


def instrument_worker() -> bool:
    """CrewAI spans. A no-op when tracing is off."""
    if _client is None:
        return False
    return _instrument("crewai", "openinference.instrumentation.crewai", "CrewAIInstrumentor")


@contextlib.contextmanager
def run_trace(
    run_id: str,
    goal_title: str = "",
    *,
    role: str | None = None,
    task_id: str | None = None,
) -> Iterator[None]:
    """Root span for one run (or one worker task). Orchestrator and workers share session id = run_id."""
    if _client is None:
        yield
        return
    stack = contextlib.ExitStack()
    try:
        from langfuse import propagate_attributes

        who = role or _role or "orchestrator"
        metadata = {"run_id": run_id}
        if task_id:
            metadata["task_id"] = task_id
        if goal_title:
            metadata["goal"] = goal_title[:120]
        stack.enter_context(
            propagate_attributes(
                session_id=run_id,
                trace_name=TRACE_NAME if who == "orchestrator" else f"{TRACE_NAME}-{who}",
                tags=["treefleet", who],
                metadata=metadata,
            )
        )
        stack.enter_context(_client.start_as_current_observation(name=TRACE_NAME, as_type="span"))
    except Exception as exc:
        log.warning("run trace not started: %s", type(exc).__name__)
        _close(stack, None)
        stack = contextlib.ExitStack()
    try:
        yield
    except BaseException as exc:
        _close(stack, exc)
        raise
    else:
        _close(stack, None)


def _close(stack: contextlib.ExitStack, exc: BaseException | None) -> None:
    try:
        if exc is None:
            stack.close()
        else:
            stack.__exit__(type(exc), exc, exc.__traceback__)
    except Exception as err:
        log.warning("trace close failed: %s", type(err).__name__)


class SpanHandle:
    """Records the outcome of one cursor-agent run on its span. Every method is safe to call when tracing is off."""

    def __init__(self, span: Any = None) -> None:
        self._span = span

    def finish(
        self, *, duration_s: float, usage: Any = None, exit_code: int | None = None, timed_out: bool = False
    ) -> None:
        if self._span is None:
            return
        try:
            details: dict[str, int] = {}
            if usage is not None:
                details = {
                    "input": int(usage.input_tokens),
                    "output": int(usage.output_tokens),
                    "cache_read_input_tokens": int(usage.cache_read_tokens),
                    "cache_write_input_tokens": int(usage.cache_write_tokens),
                }
            meta: dict[str, Any] = {"duration_s": round(float(duration_s), 3), "timed_out": bool(timed_out)}
            if exit_code is not None:
                meta["exit_code"] = int(exit_code)
            fields: dict[str, Any] = {"metadata": meta}
            if details:
                fields["usage_details"] = details
            self._span.update(**fields)
        except Exception as exc:
            log.warning("cursor span update failed: %s", type(exc).__name__)


@contextlib.contextmanager
def cursor_span(*, model: str | None, task_id: str | None = None) -> Iterator[SpanHandle]:
    """One cursor-agent invocation as a manual generation span. Never affects the Cursor run."""
    if _client is None:
        yield SpanHandle()
        return
    cm: Any = None
    handle = SpanHandle()
    try:
        cm = _client.start_as_current_observation(
            name=CURSOR_SPAN_NAME,
            as_type="generation",
            model=model or "cursor-default",
            metadata={"task_id": task_id} if task_id else None,
        )
        handle = SpanHandle(cm.__enter__())
    except Exception as exc:
        cm = None
        log.warning("cursor span not started: %s", type(exc).__name__)
    try:
        yield handle
    except BaseException as exc:
        _leave(cm, exc)
        raise
    else:
        _leave(cm, None)


def _leave(cm: Any, exc: BaseException | None) -> None:
    if cm is None:
        return
    try:
        if exc is None:
            cm.__exit__(None, None, None)
        else:
            cm.__exit__(type(exc), exc, exc.__traceback__)
    except Exception as err:
        log.warning("cursor span close failed: %s", type(err).__name__)


def flush(timeout_s: float = FLUSH_TIMEOUT_S) -> None:
    """Send queued spans. Bounded by a timeout and never raises."""
    client = _client
    if client is None:
        return

    def _go() -> None:
        try:
            client.flush()
        except Exception as exc:
            log.warning("langfuse flush failed: %s", type(exc).__name__)

    thread = threading.Thread(target=_go, name="langfuse-flush", daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        log.warning("langfuse flush timed out")


def reset_for_tests() -> None:
    """Forget module state. Tests only."""
    global _client, _role
    _client = None
    _role = ""
    _instrumented.clear()
