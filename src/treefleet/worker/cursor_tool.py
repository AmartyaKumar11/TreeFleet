"""CrewAI tool that delegates to the cursor runner."""

from __future__ import annotations

from pathlib import Path

from crewai.tools import tool

from treefleet.contracts import CursorUsage
from treefleet.cursor_run import cursor_instruction, run_cursor
from treefleet.events import EventSink


def make_cursor_tool(
    *,
    cli: Path,
    worktree: Path,
    allowed: list[str],
    denied: list[str],
    model: str | None,
    timeout_s: float,
    sink: EventSink | None = None,
    run_id: str | None = None,
    usages: list[CursorUsage] | None = None,
    files: list[str] | None = None,
    revision: str = "",
    saboteur: bool = False,
):
    launched = 0

    @tool("cursor_code")
    def cursor_code(instruction: str) -> str:
        """Edit files inside the worktree by directing cursor-agent. Returns JSON."""
        nonlocal launched
        if launched >= 2:
            return '{"ok": false, "error": "cursor_code already ran twice. Call git_diff and return notes."}'
        launched += 1
        result = run_cursor(
            cli=cli,
            worktree=worktree,
            instruction=cursor_instruction(instruction, revision=revision, saboteur=saboteur),
            allowed=allowed,
            denied=denied,
            model=model,
            timeout_s=timeout_s,
            sink=sink,
            run_id=run_id,
        )
        if usages is not None and result.usage is not None:
            usages.append(result.usage)
        if files is not None:
            files.extend(result.files_changed)
        return result.model_dump_json()

    return cursor_code
