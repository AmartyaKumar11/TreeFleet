"""Secret redaction. The secret never leaves this function in a return value."""

from __future__ import annotations

import re
from typing import Any

_SECRET = re.compile(
    r"sk-[A-Za-z0-9]{8,}"
    r"|apikey_[A-Za-z0-9]{8,}"
    r"|Bearer\s+[A-Za-z0-9._\-]{8,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    re.IGNORECASE,
)


def redact(text: str) -> tuple[str, int]:
    count = 0

    def repl(_match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "[redacted]"

    return _SECRET.sub(repl, text), count


def redact_payload(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        total = 0
        cleaned: dict[Any, Any] = {}
        for key, item in value.items():
            new_key, key_count = redact(str(key))
            new_item, item_count = redact_payload(item)
            total += key_count + item_count
            cleaned[new_key] = new_item
        return cleaned, total
    if isinstance(value, list):
        total = 0
        cleaned_list = []
        for item in value:
            new_item, item_count = redact_payload(item)
            total += item_count
            cleaned_list.append(new_item)
        return cleaned_list, total
    return value, 0
