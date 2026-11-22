"""Transcript API helpers. Raw events by byte offset; no grouping."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from treefleet.contracts import Plan
from treefleet.events import BadCursorError, OffsetPage, read_events_offset
from treefleet.guards.text import redact_payload
from treefleet.sessions import load_run

RUN_ID_RE = re.compile(r"^[a-z0-9]{6,12}$")
TRANSCRIPT_SCHEMA = "treefleet.transcript.v1"
DEFAULT_LIMIT = 200
MAX_LIMIT = 500
PAYLOAD_STRING_CAP = 4_096
PLAN_STRING_CAP = 8_192


def valid_run_id(run_id: str) -> bool:
    return bool(RUN_ID_RE.fullmatch(run_id))


def _cap_strings(value: Any, limit: int, flag: list[bool]) -> Any:
    if isinstance(value, str):
        if len(value) > limit:
            flag[0] = True
            return value[:limit]
        return value
    if isinstance(value, dict):
        return {key: _cap_strings(item, limit, flag) for key, item in value.items()}
    if isinstance(value, list):
        return [_cap_strings(item, limit, flag) for item in value]
    return value


def shape_item(raw: dict[str, Any]) -> dict[str, Any]:
    """One transcript item from an offset-page row. Caps payload strings at 4 KB."""
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    truncated = [False]
    capped = _cap_strings(payload, PAYLOAD_STRING_CAP, truncated)
    item = {
        "offset": int(raw.get("offset") or 0),
        "seq": int(raw.get("seq") or 0),
        "ts": raw.get("ts"),
        "source": raw.get("source"),
        "type": raw.get("type"),
        "task_id": raw.get("task_id"),
        "payload": capped,
    }
    if truncated[0]:
        item["truncated"] = True
    return item


def plan_payload(plan: Plan | dict[str, Any] | None) -> dict[str, Any] | None:
    """Full plan for plan=1: id, title, instructions, rationale, depends_on, checks, allowed_paths."""
    if plan is None:
        return None
    data = plan.model_dump(mode="json") if isinstance(plan, Plan) else dict(plan)
    truncated = [False]
    goal = _cap_strings(str(data.get("goal") or ""), PLAN_STRING_CAP, truncated)
    rationale = _cap_strings(str(data.get("rationale") or ""), PLAN_STRING_CAP, truncated)
    tasks: list[dict[str, Any]] = []
    for raw in data.get("tasks") or []:
        if not isinstance(raw, dict):
            continue
        checks = raw.get("check_ids") if raw.get("check_ids") is not None else raw.get("checks") or []
        tasks.append(
            {
                "id": raw.get("id"),
                "title": _cap_strings(str(raw.get("title") or ""), PLAN_STRING_CAP, truncated),
                "instructions": _cap_strings(str(raw.get("instructions") or ""), PLAN_STRING_CAP, truncated),
                "depends_on": list(raw.get("depends_on") or []),
                "checks": list(checks) if isinstance(checks, list) else checks,
                "allowed_paths": list(raw.get("allowed_paths") or []),
            }
        )
    body: dict[str, Any] = {"goal": goal, "rationale": rationale, "tasks": tasks}
    if truncated[0]:
        body["truncated"] = True
    return body


def load_plan_for_run(
    runs: Path,
    run_id: str,
    *,
    gate_plan: Plan | None = None,
) -> dict[str, Any] | None:
    if gate_plan is not None:
        return plan_payload(gate_plan)
    record_path = runs / run_id / "run.json"
    if not record_path.is_file():
        return None
    try:
        record = load_run(record_path)
    except (OSError, ValueError):
        return None
    return plan_payload(record.plan)


def build_transcript(
    path: Path,
    *,
    run_id: str,
    after: int,
    limit: int,
    include_plan: bool,
    state: str,
    gate_plan: Plan | None = None,
    awaiting: bool = False,
) -> dict[str, Any]:
    page: OffsetPage = read_events_offset(path, after, limit)
    items = [shape_item(row) for row in page.items]
    body: dict[str, Any] = {
        "schema_id": TRANSCRIPT_SCHEMA,
        "run_id": run_id,
        "after": after,
        "next": page.next_offset,
        "eof": page.eof,
        "reset": page.reset,
        "skipped": page.skipped,
        "size": page.size,
        "state": state,
        "items": items,
    }
    if include_plan:
        # path is runs/<id>/events.jsonl → parent.parent is the runs root
        body["plan"] = load_plan_for_run(path.parent.parent, run_id, gate_plan=gate_plan)
        body["awaiting"] = awaiting
    clean, _count = redact_payload(body)
    return clean if isinstance(clean, dict) else body


def parse_limit(raw: str | int | None) -> int:
    if raw is None or raw == "":
        return DEFAULT_LIMIT
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if value < 1 or value > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    return value


def parse_after(raw: str | int | None) -> int:
    if raw is None or raw == "":
        return 0
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("after must be an integer byte offset") from exc
    if value < 0:
        raise ValueError("after must be >= 0")
    return value


__all__ = [
    "BadCursorError",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "PAYLOAD_STRING_CAP",
    "PLAN_STRING_CAP",
    "TRANSCRIPT_SCHEMA",
    "build_transcript",
    "load_plan_for_run",
    "parse_after",
    "parse_limit",
    "plan_payload",
    "shape_item",
    "valid_run_id",
]
