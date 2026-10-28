"""Cursor project files in a worktree. Untracked ones block the task. Tracked ones are logged only."""

from __future__ import annotations

from pathlib import Path

from treefleet.contracts import RunEvent
from treefleet.events import EventSink
from treefleet.git.worktrees import run_git

_CAP = 2000


class ScopeViolation(RuntimeError):
    """A worktree has Cursor config that is not in the base commit."""


def check_cursor_scope(
    worktree: Path, *, run_id: str, sink: EventSink | None, task_id: str | None = None
) -> list[str]:
    """Return warnings for tracked Cursor files. Raise when an untracked one is present."""
    tracked = _tracked(worktree)
    warnings: list[str] = []
    violations: list[str] = []
    for path in _candidates(worktree):
        rel = path.relative_to(worktree).as_posix()
        if rel in tracked:
            text = path.read_text(encoding="utf-8", errors="replace")[:_CAP]
            warnings.append(f"{rel}: {text}")
            _emit(sink, run_id, "git.cursor_warning", {"path": rel, "text": text}, task_id)
        else:
            violations.append(rel)
            _emit(sink, run_id, "scope_violation", {"path": rel, "reason": "untracked cursor config"}, task_id)
    cursor = worktree / ".cursor"
    if cursor.is_dir() and not any(item.startswith(".cursor/") for item in tracked) and ".cursor" not in violations:
        if not any(item.startswith(".cursor/") for item in violations):
            violations.append(".cursor")
            _emit(sink, run_id, "scope_violation", {"path": ".cursor", "reason": "untracked cursor config"}, task_id)
    if violations:
        raise ScopeViolation("untracked cursor config: " + ", ".join(violations))
    return warnings


def _candidates(worktree: Path) -> list[Path]:
    found: list[Path] = []
    rules = worktree / ".cursorrules"
    if rules.is_file():
        found.append(rules)
    cursor = worktree / ".cursor"
    if cursor.is_dir():
        found.extend(path for path in cursor.rglob("*") if path.is_file())
    return found


def _tracked(worktree: Path) -> set[str]:
    proc = run_git(["ls-files"], cwd=worktree)
    return {line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()}


def _emit(
    sink: EventSink | None,
    run_id: str,
    event_type: str,
    payload: dict[str, str],
    task_id: str | None = None,
) -> None:
    if sink is None:
        return
    sink.emit_sync(
        RunEvent(run_id=run_id, seq=0, source="git", type=event_type, task_id=task_id, payload=payload)
    )
