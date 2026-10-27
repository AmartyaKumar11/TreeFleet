"""Worker draft checks. CrewAI retries with the returned reason."""

from __future__ import annotations

import json
import re
from pathlib import Path

from treefleet.guards.text import redact

_FILE = re.compile(r"[\w./\\-]+\.(?:py|ts|tsx|js|jsx|md|json|toml|yml|yaml)")


def draft_problems(text: str, changed: list[str]) -> str | None:
    _cleaned, count = redact(text)
    if count:
        return "notes contain a secret"
    start = text.find("{")
    end = text.rfind("}")
    body = text[start : end + 1] if start >= 0 and end > start else text
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return "return WorkerDraft JSON with a non-empty summary"
    summary = data.get("summary")
    notes = str(data.get("notes") or "")
    if summary is not None and not str(summary).strip():
        return "summary is empty"
    if not notes.strip():
        return "summary is empty"
    names = {Path(path).name for path in changed}
    for match in _FILE.finditer(notes):
        name = Path(match.group(0)).name
        if name not in names:
            return f"claims {name} which is not in git_diff"
    return None
