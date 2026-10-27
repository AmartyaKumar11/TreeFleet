"""Work that must finish before a worker process is created."""

from __future__ import annotations

from pathlib import Path

from treefleet.events import EventSink
from treefleet.git.cursor_scope import check_cursor_scope


def before_spawn(worktree: Path, run_id: str, sink: EventSink | None = None) -> list[str]:
    """Fail the task when the worktree has untracked Cursor config. Return warnings for tracked files."""
    return check_cursor_scope(worktree, run_id=run_id, sink=sink)
