"""Re-arm one comment so the next poll can start a round. It does not poll or post."""

from __future__ import annotations

from treefleet.sessions import RunRecord

_REARM = frozenset({"worker.start_failed", "check.unavailable"})


def last_failure(events: list[dict[str, object]], task_id: str) -> tuple[str, str]:
    """The latest start, check, or revision failure for one task. Kind, then reason."""
    kind = ""
    reason = ""
    for event in events:
        if event.get("task_id") != task_id:
            continue
        etype = str(event.get("type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        body = payload if isinstance(payload, dict) else {}
        if etype in _REARM or etype == "pr.revision_failed":
            kind = etype
            reason = str(body.get("reason") or "")
        elif etype == "pr.checks" and body.get("status") == "needs_review":
            kind = etype
            reason = str(body.get("reason") or reason)
    return kind, reason


def rearm_comment(
    record: RunRecord,
    events: list[dict[str, object]],
    pr_number: int,
) -> tuple[bool, str, RunRecord]:
    """Reset one needs_human comment, or refuse. The record is unchanged on a refusal."""
    task = next((row for row in record.tasks if row.pr_number == pr_number), None)
    if task is None:
        return False, f"no pull request {pr_number}", record
    waiting = next((row for row in task.comments if row.status == "needs_human"), None)
    if waiting is None:
        armed = next((row for row in task.comments if row.status == "open" and row.rounds_used == 0), None)
        if armed is not None and task.pr_last_comment_id < armed.id:
            return False, f"comment {armed.id} already open, rounds_used 0", record
        return False, f"pull request {pr_number} has no needs_human comment", record
    kind, reason = last_failure(events, task.task_id)
    if kind not in _REARM:
        why = reason or kind or "unknown failure"
        return False, f"refusing retry: comment {waiting.id} is needs_human after {why}", record
    comments = [
        row.model_copy(update={"status": "open", "rounds_used": 0}) if row.id == waiting.id else row
        for row in task.comments
    ]
    updated = task.model_copy(
        update={
            "comments": comments,
            "pr_status": "open",
            "pr_last_comment_id": waiting.id - 1,
            "reason": "",
        }
    )
    tasks = [updated if row.task_id == task.task_id else row for row in record.tasks]
    changed = record.model_copy(update={"tasks": tasks})
    return True, f"comment {waiting.id} on pull request {pr_number} reset to open, rounds_used 0", changed
