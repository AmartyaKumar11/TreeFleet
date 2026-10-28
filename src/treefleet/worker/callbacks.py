"""CrewAI callbacks. Events carry counts and durations, never a prompt or a secret."""

from __future__ import annotations

import json
import time
from typing import Any

from treefleet.contracts import RunEvent
from treefleet.cursor_run import redact_line
from treefleet.events import EventSink
from treefleet.guards.paths import path_violation
from treefleet.guards.text import redact
from treefleet.mcp_servers.worktree_fs_server import PathGuardError, resolve_inside


def emit_output_line(sink: EventSink, run_id: str, text: str) -> RunEvent:
    from treefleet.contracts import OutputLine

    line = OutputLine(line=redact_line(text))
    return sink.emit_sync(
        RunEvent(run_id=run_id, seq=0, source="worker", type="output_line", payload=line.model_dump())
    )


def emit_step(sink: EventSink | None, run_id: str, task_id: str | None, name: str, started: float) -> None:
    if sink is None:
        return
    sink.emit_sync(
        RunEvent(
            run_id=run_id,
            seq=0,
            source="worker",
            type="worker.step_callback",
            task_id=task_id,
            payload={"name": name, "duration_s": round(time.monotonic() - started, 3)},
        )
    )


def emit_task(sink: EventSink | None, run_id: str, task_id: str | None, started: float) -> None:
    if sink is None:
        return
    sink.emit_sync(
        RunEvent(
            run_id=run_id,
            seq=0,
            source="worker",
            type="worker.task_callback",
            task_id=task_id,
            payload={"duration_s": round(time.monotonic() - started, 3)},
        )
    )


def on_tool_args(
    name: str,
    args: dict[str, Any],
    *,
    root: Any,
    checks: dict[str, str],
    sink: EventSink | None,
    run_id: str,
    task_id: str | None,
) -> str | None:
    message = _arg_problem(name, args, root, checks)
    if message is None:
        return None
    if sink is not None:
        sink.emit_sync(
            RunEvent(
                run_id=run_id,
                seq=0,
                source="guard",
                type="guard.decision",
                task_id=task_id,
                payload={
                    "stage": "tool_args",
                    "allowed": False,
                    "detector": "deterministic",
                    "reasons": [message[:200]],
                },
            )
        )
    return json.dumps({"ok": False, "error": {"code": "PATH_ESCAPE", "message": message[:200]}})


def on_tool_result(raw: str, sink: EventSink | None, run_id: str, task_id: str | None) -> str:
    text, count = redact(raw)
    if count and sink is not None:
        sink.emit_sync(
            RunEvent(
                run_id=run_id,
                seq=0,
                source="guard",
                type="guard_redacted",
                task_id=task_id,
                payload={"count": count, "stage": "tool_result"},
            )
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and "ok" in data:
        return text
    reason = " ".join(text.split())[:300] or "empty tool result"
    if sink is not None:
        sink.emit_sync(
            RunEvent(
                run_id=run_id,
                seq=0,
                source="guard",
                type="guard.decision",
                task_id=task_id,
                payload={
                    "stage": "tool_result",
                    "allowed": False,
                    "detector": "deterministic",
                    "reasons": [reason],
                },
            )
        )
    return json.dumps({"ok": False, "error": {"code": "BAD_ARGS", "message": reason}})


def _arg_problem(name: str, args: dict[str, Any], root: Any, checks: dict[str, str]) -> str | None:
    if name == "run_check":
        check = str(args.get("name") or "")
        if check not in checks:
            return "run_check accepts only a named check"
        return None
    for key in ("path", "file", "target"):
        if key not in args:
            continue
        raw = str(args[key])
        why = path_violation(raw)
        if why:
            return f"{key} rejected: {why}"
        try:
            resolve_inside(root, raw)
        except (PathGuardError, ValueError, OSError) as exc:
            return str(exc)[:200]
    return None
