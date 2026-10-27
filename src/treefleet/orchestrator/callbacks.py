"""ADK callbacks. They emit events and never return a prompt or a secret."""

from __future__ import annotations

import time
from typing import Any

from treefleet.contracts import RunEvent, TokenUsage
from treefleet.events import EventSink


class UsageBox:
    def __init__(self) -> None:
        self.usage = TokenUsage()
        self.calls = 0


def _emit(sink: EventSink, run_id: str, kind: str, payload: dict[str, Any], task_id: str | None = None) -> None:
    sink.emit_sync(
        RunEvent(
            run_id=run_id,
            seq=0,
            source="orchestrator",
            type=kind,
            task_id=task_id,
            payload=payload,
        )
    )


def agent_hooks(sink: EventSink, run_id: str, name: str) -> tuple[Any, Any]:
    started = [time.monotonic()]

    def before(*_args: object, **_kwargs: object) -> None:
        started[0] = time.monotonic()
        _emit(sink, run_id, "orchestrator.agent_start", {"name": name, "duration_s": 0})
        return None

    def after(*_args: object, **_kwargs: object) -> None:
        _emit(
            sink,
            run_id,
            "orchestrator.agent_end",
            {"name": name, "duration_s": round(time.monotonic() - started[0], 3)},
        )
        return None

    return before, after


def model_hooks(sink: EventSink, run_id: str, role: str, box: UsageBox) -> tuple[Any, Any]:
    started = [time.monotonic()]

    def before(*_args: object, **kwargs: object) -> None:
        started[0] = time.monotonic()
        request = kwargs.get("llm_request")
        chars = len(str(request)) if request is not None else 0
        _emit(sink, run_id, "orchestrator.model_call", {"role": role, "phase": "before", "chars": chars})
        return None

    def after(*_args: object, **kwargs: object) -> None:
        box.calls += 1
        found = _usage_from(kwargs.get("llm_response"))
        if found is not None:
            box.usage = box.usage + found
        _emit(
            sink,
            run_id,
            "orchestrator.model_call",
            {
                "role": role,
                "phase": "after",
                "duration_s": round(time.monotonic() - started[0], 3),
                "input_tokens": box.usage.input_tokens,
                "output_tokens": box.usage.output_tokens,
            },
        )
        return None

    return before, after


def tool_hooks(sink: EventSink, run_id: str) -> tuple[Any, Any]:
    started = [time.monotonic()]

    def before(*_args: object, **kwargs: object) -> None:
        started[0] = time.monotonic()
        name = str(kwargs.get("tool_name") or kwargs.get("tool") or "tool")
        _emit(sink, run_id, "orchestrator.tool_call", {"name": name, "phase": "before"})
        return None

    def after(*_args: object, **kwargs: object) -> None:
        name = str(kwargs.get("tool_name") or kwargs.get("tool") or "tool")
        _emit(
            sink,
            run_id,
            "orchestrator.tool_result",
            {"name": name, "phase": "after", "duration_s": round(time.monotonic() - started[0], 3)},
        )
        return None

    return before, after


def _usage_from(response: object) -> TokenUsage | None:
    if response is None:
        return None
    meta = getattr(response, "usage_metadata", None) or getattr(response, "usage", None)
    if meta is None:
        return None
    prompt = getattr(meta, "prompt_token_count", None)
    output = getattr(meta, "candidates_token_count", None)
    if prompt is None:
        prompt = getattr(meta, "input_tokens", 0)
    if output is None:
        output = getattr(meta, "output_tokens", 0)
    return TokenUsage(input_tokens=int(prompt or 0), output_tokens=int(output or 0))
