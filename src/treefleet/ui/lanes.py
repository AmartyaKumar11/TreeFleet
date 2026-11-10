"""Board lane derivation. The page renders the lane field; it does not guess."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

Lane = Literal["needs", "working", "review", "ready", "done"]

# A later event of one of these kinds means the task kept going after a tool denial.
_CONTINUED = {
    "output_line",
    "cursor.tool_start",
    "cursor.tool_finish",
    "worker.step",
    "worker.step_callback",
    "worker.task_callback",
    "review_started",
    "review_verdict",
    "revise_started",
}
_STOPPED = {"failed", "needs_review"}


@dataclass(frozen=True)
class TaskLaneFacts:
    """Facts the board helper reads. All optional defaults are false or empty."""

    awaiting_approval: bool = False
    status: str = ""
    verdict: str = ""
    checks_failed: bool = False
    conflict: bool = False
    guard_blocked: bool = False
    needs_human: bool = False
    worker_active: bool = False
    output_seen: bool = False
    fleet_in_progress: bool = False
    review_started: bool = False
    awaiting_verdict: bool = False
    integrated: bool = False
    ready_to_merge: bool = False
    pr_open: bool = False
    pr_revising: bool = False
    pr_green: bool = False
    pr_checks: str = ""
    pr_status: str = ""
    pr_enabled: bool = False


def _failed_blocks(facts: TaskLaneFacts) -> bool:
    """An earlier worker failure loses to a later approval when nothing is waiting on a person."""
    if facts.status != "failed":
        return False
    if facts.verdict == "approve":
        return False
    quiet = facts.pr_status == "approved_green" and not facts.needs_human and not facts.pr_revising
    return not quiet


def derive_lane(facts: TaskLaneFacts) -> Lane:
    """Map facts to a board lane id. The latest pr state outranks an earlier approval."""
    if facts.pr_status == "merged":
        return "done"
    if facts.pr_status == "cancelled":
        return "needs"
    if (
        facts.awaiting_approval
        or facts.status == "needs_review"
        or (facts.checks_failed and not facts.pr_open)
        or (facts.conflict and not facts.pr_open)
        or facts.guard_blocked
        or facts.needs_human
        or facts.verdict in {"revise", "reject"}
        or _failed_blocks(facts)
    ):
        return "needs"
    if facts.pr_revising:
        return "working"
    if facts.pr_open and not facts.pr_green:
        return "review"
    # A pull-request run is not Ready until the PR is open, green, and quiet.
    if facts.pr_enabled and facts.verdict == "approve" and facts.pr_status != "approved_green":
        return "review"
    if facts.pr_green or facts.verdict == "approve":
        return "ready"
    if facts.review_started or facts.awaiting_verdict or facts.status == "done":
        return "review"
    if facts.worker_active or facts.output_seen or facts.fleet_in_progress:
        return "working"
    return "working"


def _argument_error(payload: dict[str, Any]) -> bool:
    """A tool-argument failure is not a policy block, including old tool_result denials."""
    from treefleet.events import argument_failure

    return argument_failure(payload)


def _policy_denial(payload: dict[str, Any]) -> bool:
    return payload.get("allowed") is False and not _argument_error(payload)


def _checks_failed(result: dict[str, Any]) -> bool:
    checks = result.get("checks") or []
    if not isinstance(checks, list):
        return False
    return any(isinstance(item, dict) and item.get("passed") is False for item in checks)


def _conflicted(task_id: str, report: dict[str, Any] | None) -> bool:
    if not report:
        return False
    integration = report.get("integration") or {}
    if not isinstance(integration, dict):
        return False
    for item in integration.get("conflicts") or []:
        if isinstance(item, dict) and task_id in (item.get("between") or []):
            return True
    for line in integration.get("excluded") or []:
        if isinstance(line, str) and line.startswith(f"{task_id}:") and "conflict" in line:
            return True
    return False


def _integrated(task_id: str, report: dict[str, Any] | None) -> bool:
    if not report:
        return False
    integration = report.get("integration") or {}
    if not isinstance(integration, dict):
        return False
    merged = integration.get("merged") or []
    return isinstance(merged, list) and task_id in merged


def tasks_from_snapshot(
    events: list[dict[str, Any]],
    report: dict[str, Any] | None,
    *,
    awaiting: bool = False,
    pr_enabled: bool = False,
) -> list[dict[str, Any]]:
    """Build task cards with a server-computed lane for each known task id."""
    by_id: dict[str, dict[str, Any]] = {}
    fleet_in_progress = False
    for event in events:
        kind = str(event.get("type") or "")
        raw_payload = event.get("payload")
        payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
        if kind == "phase" and payload.get("name") == "fleet":
            fleet_in_progress = True
        if kind in {"worker.spawned", "output_line", "cursor.tool_start"}:
            fleet_in_progress = True
        if kind == "phase" and payload.get("name") in {"review", "revise", "integrate", "report"}:
            fleet_in_progress = False
        task_id = event.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            continue
        task = by_id.setdefault(
            task_id,
            {
                "id": task_id,
                "title": task_id,
                "status": "",
                "verdict": "",
                "model": "",
                "round": 0,
                "lines": [],
                "worker_active": False,
                "output_seen": False,
                "review_started": False,
                "awaiting_verdict": False,
                "guard_blocked": False,
                "policy_open": False,
                "needs_human": False,
            },
        )
        if kind == "tool.error":
            pass
        elif kind == "guard.decision" and _policy_denial(payload):
            task["policy_open"] = True
        if kind == "reaction.fired" and str(payload.get("action") or "") == "needs_human":
            task["needs_human"] = True
        if kind == "model_selected":
            task["model"] = str(payload.get("model") or task["model"])
        if kind == "worker.spawned":
            task["worker_active"] = True
            run_id = str(event.get("run_id") or "")
            if run_id and not task.get("branch"):
                task["branch"] = f"tf/{run_id}/{task_id}"
        if kind == "output_line":
            task["output_seen"] = True
            task["worker_active"] = True
            line = payload.get("line")
            if isinstance(line, str) and line:
                task["lines"].append(line)
        if kind in {"cursor.tool_start", "cursor.tool_finish"}:
            task["worker_active"] = True
            tool = str(payload.get("tool") or "tool")
            verb = "editing" if tool == "edit" else tool
            path = str(payload.get("path") or "")
            done = ""
            if kind == "cursor.tool_finish":
                done = " done" if payload.get("ok") else " failed"
            line = f"{verb} {path}{done}".strip()
            if line:
                task["lines"].append(line)
            if kind == "cursor.tool_start" and path:
                task["action"] = f"{verb} {path}"
        if kind == "worker.exited":
            task["worker_active"] = False
            task["status"] = str(payload.get("status") or task["status"])
            if payload.get("error"):
                task["error"] = payload["error"]
            if payload.get("files"):
                task["files"] = payload["files"]
            if task["status"] in _STOPPED and task.get("policy_open"):
                task["guard_blocked"] = True
            else:
                task["policy_open"] = False
                if task["status"] not in _STOPPED:
                    task["guard_blocked"] = False
        if kind in _CONTINUED:
            task["policy_open"] = False
            task["guard_blocked"] = False
        if kind == "review_started":
            task["review_started"] = True
            task["awaiting_verdict"] = True
        if kind == "review_verdict":
            task["awaiting_verdict"] = False
            task["verdict"] = str(payload.get("verdict") or task["verdict"])
            if payload.get("status"):
                task["status"] = str(payload["status"])
            if payload.get("reason"):
                task["reason"] = payload["reason"]
            raw_notes = payload.get("suggestions")
            if isinstance(raw_notes, list):
                task["suggestions"] = [str(item) for item in raw_notes if isinstance(item, str) and item][:20]
        if kind == "revise_started":
            task["round"] = int(payload.get("round") or task["round"] or 0)
            task["worker_active"] = True
        if kind == "pr.opened":
            task["pr_number"] = payload.get("number")
            task["pr_url"] = str(payload.get("url") or "")
            task["pr_open"] = True
            task["pr_checks"] = "pending"
            task["pr_status"] = "open"
        if kind == "pr.revision_started":
            task["pr_revising"] = True
            task["pr_open"] = True
            task["pr_status"] = "open"
            task["pr_checks"] = "pending"
            task["worker_active"] = True
            if task.get("status") == "needs_review":
                task["status"] = "open"
                task["needs_human"] = False
        if kind == "pr.revision_pushed":
            task["pr_revising"] = False
            task["worker_active"] = False
            task["pr_checks"] = "pending"
            if task.get("reason") == "revision made no commit":
                task["reason"] = ""
        if kind == "pr.revision_failed":
            task["pr_revising"] = False
            task["worker_active"] = False
            task["reason"] = str(payload.get("reason") or "revision failed")
        if kind in {"pr.checks", "pr.checks_green"}:
            if payload.get("checks"):
                task["pr_checks"] = str(payload["checks"])
            approved = payload.get("approved")
            if approved is True or (
                payload.get("checks") == "passing" and "approved" not in payload and kind == "pr.checks"
            ):
                task["pr_status"] = "approved_green"
                task["pr_open"] = True
            if payload.get("status") == "needs_review":
                task["status"] = "needs_review"
                task["needs_human"] = True
                if payload.get("reason"):
                    task["reason"] = str(payload["reason"])
        if kind == "pr.merged":
            task["status"] = "done"
            task["pr_open"] = False
            task["pr_status"] = "merged"
            task["pr_checks"] = "passing"
        if kind == "pr.closed":
            task["status"] = "cancelled"
            task["pr_open"] = False
            task["pr_status"] = "cancelled"
            task["reason"] = str(payload.get("reason") or "pull request closed")
        if kind == "worker.start_failed":
            task["status"] = "needs_review"
            task["needs_human"] = True
            task["pr_revising"] = False
            task["worker_active"] = False
            reason = str(payload.get("reason") or "worker exited")
            task["reason"] = f"worker could not start: {reason}"
        if kind == "check.unavailable":
            task["status"] = "needs_review"
            task["needs_human"] = True
            task["pr_revising"] = False
            task["reason"] = str(payload.get("reason") or "check could not run")
        if kind == "pr.poll_failed":
            task["status"] = "needs_review"
            task["reason"] = "PR polling failed"
            task["needs_human"] = True
        if kind == "pr.comment_ignored":
            reason = str(payload.get("reason") or "untrusted")
            if reason in {"untrusted", "stale"}:
                task["notice"] = f"comment ignored: {reason}"
        if kind == "acceptance.unavailable":
            task["status"] = "needs_review"
            task["needs_human"] = True
            task["pr_revising"] = False
            task["reason"] = str(payload.get("reason") or "reviewer unavailable")
        if kind == "pr.trust_unresolved":
            task["status"] = "needs_review"
            task["needs_human"] = True
            task["reason"] = str(payload.get("reason") or "authenticated login could not be resolved")
            task["notice"] = task["reason"]

    if report and isinstance(report.get("results"), list):
        for result in report["results"]:
            if not isinstance(result, dict):
                continue
            task_id = result.get("task_id")
            if not isinstance(task_id, str):
                continue
            task = by_id.setdefault(
                task_id,
                {
                    "id": task_id,
                    "title": task_id,
                    "status": "",
                    "verdict": "",
                    "model": "",
                    "round": 0,
                    "lines": [],
                    "worker_active": False,
                    "output_seen": False,
                    "review_started": False,
                    "awaiting_verdict": False,
                    "guard_blocked": False,
                    "policy_open": False,
                    "needs_human": False,
                },
            )
            task["status"] = str(result.get("status") or task["status"])
            if task["status"] in _STOPPED and task.get("policy_open"):
                task["guard_blocked"] = True
            task["checks_failed"] = _checks_failed(result)
            if result.get("error"):
                task["error"] = result["error"]

    tasks: list[dict[str, Any]] = []
    for task_id, task in by_id.items():
        facts = TaskLaneFacts(
            awaiting_approval=awaiting,
            status=str(task.get("status") or ""),
            verdict=str(task.get("verdict") or ""),
            checks_failed=bool(task.get("checks_failed")),
            conflict=_conflicted(task_id, report),
            guard_blocked=bool(task.get("guard_blocked")),
            needs_human=bool(task.get("needs_human")),
            worker_active=bool(task.get("worker_active")),
            output_seen=bool(task.get("output_seen")),
            fleet_in_progress=fleet_in_progress and not task.get("status"),
            review_started=bool(task.get("review_started")),
            awaiting_verdict=bool(task.get("awaiting_verdict")),
            integrated=_integrated(task_id, report),
            ready_to_merge=_integrated(task_id, report),
            pr_open=bool(task.get("pr_open")),
            pr_revising=bool(task.get("pr_revising")),
            pr_green=task.get("pr_status") == "approved_green",
            pr_checks=str(task.get("pr_checks") or ""),
            pr_status=str(task.get("pr_status") or ""),
            pr_enabled=pr_enabled,
        )
        hidden = {"worker_active", "output_seen", "checks_failed", "guard_blocked", "policy_open", "needs_human"}
        card = {key: value for key, value in task.items() if key not in hidden}
        card["lane"] = derive_lane(facts)
        if (
            pr_enabled
            and facts.verdict == "approve"
            and not facts.pr_open
            and facts.pr_status not in {"approved_green", "merged", "cancelled"}
        ):
            card["pr_chip"] = "opening PR"
        tasks.append(card)
    return tasks


def run_needs_human(
    events: list[dict[str, Any]],
    report: dict[str, Any] | None,
    *,
    awaiting: bool,
    pr_enabled: bool = False,
) -> bool:
    """True when the human must act: plan approval or any Needs you lane."""
    if awaiting:
        return True
    cards = tasks_from_snapshot(events, report, awaiting=False, pr_enabled=pr_enabled)
    return any(task.get("lane") == "needs" for task in cards)


def status_line(stage: str, tasks: list[dict[str, Any]], *, pr_mode: bool) -> tuple[str, str] | None:
    """Strip label and stepper stage while pull requests are on. None keeps the old strip."""
    if not pr_mode:
        return None
    if stage in {"fleet", "plan", "approve", "guard"}:
        return None
    if stage == "timed_out":
        return "Timed out", "pr"
    merged = sum(1 for task in tasks if task.get("pr_status") == "merged")
    if tasks and merged == len(tasks):
        return ("done" if stage == "done" else f"{merged} merged"), "pr"
    ready = sum(1 for task in tasks if task.get("lane") == "ready")
    pending = any(task.get("lane") in {"working", "review", "needs"} for task in tasks)
    if ready and not pending:
        return f"{ready} ready to merge", "pr"
    has_pr = any(task.get("pr_number") for task in tasks)
    if has_pr or stage in {"pr", "awaiting_prs"}:
        return "PRs open", "pr"
    if stage == "review":
        return "Reviewing", "review"
    if stage == "done":
        return "Done", "pr"
    return None
