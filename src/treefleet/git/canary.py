"""Untracked canary in the main checkout, excluded from status."""

from __future__ import annotations

import time
from pathlib import Path

from treefleet.git.worktrees import git_out

CANARY_NAME = ".treefleet_canary"


def write_canary(repo: Path, run_id: str) -> str:
    content = f"canary-{run_id}-{int(time.time())}"
    (repo / CANARY_NAME).write_text(content + "\n", encoding="utf-8")
    common = Path(git_out(["rev-parse", "--git-common-dir"], repo).strip())
    if not common.is_absolute():
        common = (repo / common).resolve()
    exclude = common / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if CANARY_NAME not in existing.splitlines():
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        exclude.write_text(existing + prefix + CANARY_NAME + "\n", encoding="utf-8")
    return content
