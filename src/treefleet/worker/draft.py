"""Parse WorkerDraft JSON. One repair retry, then the error propagates."""

from __future__ import annotations

import json
from collections.abc import Callable

from pydantic import ValidationError

from treefleet.contracts import WorkerDraft

_NOTES_LIMIT = 3000


def parse_draft(text: str) -> WorkerDraft:
    start = text.find("{")
    end = text.rfind("}")
    body = text[start : end + 1] if start >= 0 and end > start else text
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return WorkerDraft.model_validate_json(body)
    notes = data.get("notes") if isinstance(data, dict) else None
    if isinstance(notes, str) and len(notes) > _NOTES_LIMIT:
        data["notes"] = notes[:_NOTES_LIMIT]
    return WorkerDraft.model_validate(data)


def draft_with_retry(produce: Callable[[str | None], str]) -> WorkerDraft:
    text = produce(None)
    try:
        return parse_draft(text)
    except ValidationError as exc:
        return parse_draft(produce(str(exc)))
