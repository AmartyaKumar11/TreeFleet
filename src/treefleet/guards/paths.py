"""Path rules shared by the plan guard and the tool-arg guard."""

from __future__ import annotations

import re
from fnmatch import fnmatch

_DRIVE = re.compile(r"^[A-Za-z]:")
_SECRET_NAMES = {".env", ".env.local", ".env.production"}


def path_violation(raw: str) -> str | None:
    """Return a short reason when a plan or tool path must be rejected."""
    text = raw.replace("\\", "/")
    if text.startswith("/") or _DRIVE.match(text):
        return "absolute"
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        return "parent"
    if any(part in {".git", ".cursor"} for part in parts):
        return "protected"
    name = parts[-1] if parts else text
    if name in _SECRET_NAMES or name.startswith(".env.") or name.startswith("id_rsa") or name.endswith(".pem"):
        return "secret"
    return None


def declared(path: str, allowed: list[str]) -> bool:
    name = path.replace("\\", "/")
    for pattern in allowed:
        pat = pattern.replace("\\", "/")
        if pat.endswith("/**"):
            prefix = pat[:-3]
            if name == prefix or name.startswith(prefix + "/"):
                return True
        if name == pat or fnmatch(name, pat):
            return True
    return False
