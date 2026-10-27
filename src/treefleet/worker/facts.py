"""Summary text from git facts. The model does not write this."""

from __future__ import annotations

from treefleet.contracts import FileChange


def summary_from_files(files: list[FileChange]) -> str:
    if not files:
        return "No files changed."
    return ", ".join(f"{item.path} +{item.additions} -{item.deletions}" for item in files)
