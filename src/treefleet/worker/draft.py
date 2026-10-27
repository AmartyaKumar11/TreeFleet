"""Parse WorkerDraft JSON. One repair retry, then the error propagates."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import ValidationError

from treefleet.contracts import WorkerDraft


def parse_draft(text: str) -> WorkerDraft:
    start = text.find("{")
    end = text.rfind("}")
    body = text[start : end + 1] if start >= 0 and end > start else text
    return WorkerDraft.model_validate_json(body)


def draft_with_retry(produce: Callable[[str | None], str]) -> WorkerDraft:
    text = produce(None)
    try:
        return parse_draft(text)
    except ValidationError as exc:
        return parse_draft(produce(str(exc)))
