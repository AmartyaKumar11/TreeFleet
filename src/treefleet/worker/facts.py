"""Summary text from git facts. The model does not write this."""

from __future__ import annotations

import re
from pathlib import Path

from treefleet.contracts import FileChange

_FILE = re.compile(r"[\w./\\-]+\.(?:py|ts|tsx|js|jsx|md|json|toml|yml|yaml)")
_DENY = re.compile(
    r"no changes were made|not created or edited|no production or test files|nothing was modified",
    re.IGNORECASE,
)


def summary_from_files(files: list[FileChange]) -> str:
    if not files:
        return "No files changed."
    return ", ".join(f"{item.path} +{item.additions} -{item.deletions}" for item in files)


def reconcile_notes(notes: str, files: list[FileChange]) -> str:
    """Replace a note that disagrees with git. The claim stays, labeled unverified."""
    claim = notes.strip()
    names = {Path(item.path).name for item in files}
    missing = [match.group(0) for match in _FILE.finditer(claim) if Path(match.group(0)).name not in names]
    if not missing and not (files and _DENY.search(claim)):
        return claim
    return f"{summary_from_files(files)}. agent claim (unverified): {claim}"[:3000]
