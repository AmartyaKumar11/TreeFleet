"""Poll only the pull requests this run opened."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from treefleet.guards.text import redact
from treefleet.pr.accept import classify_check, format_revision, parse_acceptance, revision_request
from treefleet.pr.models import CheckRun, OpenPR, PRState, TrackedComment
from treefleet.pr.safety import MARKER, comment_kind, delimited_feedback, public_text
from treefleet.reactions import Action, Event, ReactionTable, default_table, resolve

_POLL_BACKOFF_S = (5.0, 15.0, 45.0)
_POLL_FAILS = 5

_DONE = frozenset({"merged", "cancelled"})


@dataclass
class PollHooks:
    """The stage supplies these. Tests pass plain functions. merge is not one of them."""

    revise: Callable[[str, str], str | None]
    resolve_conflict: Callable[[str], str | None]
    emit: Callable[[str, str, dict[str, object]], None]
    judge: Callable[[str, str, str], tuple[str, str]] | None = None
    diff: Callable[[str], str] | None = None
    events: list[tuple[str, str, dict[str, object]]] = field(default_factory=list)

    def note(self, kind: str, task_id: str, payload: dict[str, object]) -> None:
        self.events.append((kind, task_id, payload))
        self.emit(kind, task_id, payload)


def check_parts(state: PRState) -> tuple[str, list[str]]:
    """Overall label plus any check value this table does not know."""
    if not state.check_runs:
        return "pending", []
    labels: list[str] = []
    unknown: list[str] = []
    for run in state.check_runs:
        label, raw = classify_check(status=run.status, conclusion=run.conclusion)
        labels.append(label)
        if raw:
            unknown.append(raw)
    if any(item == "failing" for item in labels):
        return "failing", unknown
    if unknown or any(item != "passing" for item in labels):
        return "pending", unknown
    return "passing", unknown


def check_label(state: PRState) -> str:
    return check_parts(state)[0]


def _green(runs: tuple[CheckRun, ...]) -> bool:
    if not runs:
        return False
    return all(classify_check(status=item.status, conclusion=item.conclusion)[0] == "passing" for item in runs)


class PollItemError(Exception):
    """One pull request's poll raised. The loop records it and keeps running."""

    def __init__(self, task_id: str, cause: BaseException) -> None:
        self.task_id = task_id
        super().__init__(task_id)
        self.cause = cause


def poll_message(exc: BaseException) -> str:
    text, _count = redact(str(exc))
    return " ".join(text.split())[:300]


def poll_once(
    opened: list[OpenPR],
    provider: object,
    *,
    trusted: set[str],
    table: ReactionTable | None = None,
    auto_merge: bool = False,
    hooks: PollHooks,
) -> None:
    """Read get_pr and list_comments only for numbers in `opened`."""
    policy = table or default_table()
    for item in opened:
        if item.status in _DONE:
            continue
        try:
            _poll_item(item, provider, trusted=trusted, policy=policy, auto_merge=auto_merge, hooks=hooks)
        except Exception as exc:
            raise PollItemError(item.task_id, exc) from exc


def _poll_item(
    item: OpenPR,
    provider: object,
    *,
    trusted: set[str],
    policy: ReactionTable,
    auto_merge: bool,
    hooks: PollHooks,
) -> None:
    state = provider.get_pr(item.number)  # type: ignore[attr-defined]
    if state.head_sha and item.head and not str(item.head).startswith("tf/"):
        return
    if state.state == "merged":
        item.status = "merged"
        hooks.note("pr.merged", item.task_id, {"number": item.number})
        return
    if state.state == "closed":
        item.status = "cancelled"
        hooks.note("pr.closed", item.task_id, {"number": item.number, "reason": "pull request closed"})
        return
    comments = provider.list_comments(item.number, item.last_comment_id)  # type: ignore[attr-defined]
    acted = False
    for comment in comments:
        item.last_comment_id = max(item.last_comment_id, comment.id)
        kind = comment_kind(comment, trusted)
        if kind == "own":
            continue
        if kind == "untrusted":
            hooks.note("pr.comment_ignored", item.task_id, {"author": comment.author, "number": item.number})
            continue
        _on_comment(item, comment, provider, policy, hooks)
        acted = True
    if acted or item.status in _DONE or item.status == "needs_review":
        return
    if state.mergeable == "CONFLICTING":
        _on_conflict(item, provider, policy, hooks)
        return
    label, unknown = check_parts(state)
    for value in unknown:
        hooks.note("pr.unknown_check_state", item.task_id, {"value": value, "number": item.number})
    if label == "failing" and state.head_sha == item.head_sha and item.checks != "revising":
        _on_checks(item, provider, policy, hooks)
        return
    if (
        label == "passing"
        and state.mergeable != "CONFLICTING"
        and not item.pending_revision
        and item.status != "approved_green"
    ):
        _on_green(item, provider, policy, auto_merge, hooks)


def _attempt(item: OpenPR, name: str) -> int:
    item.rounds[name] = item.rounds.get(name, 0) + 1
    return item.rounds[name]


def _exhausted(item: OpenPR, summary: str, hooks: PollHooks) -> None:
    item.status = "needs_review"
    item.last_failure = public_text(summary)[:1800]
    hooks.note("pr.checks", item.task_id, {"status": "needs_review", "number": item.number})


def _diff(item: OpenPR, hooks: PollHooks) -> str:
    if hooks.diff is None:
        return ""
    return hooks.diff(item.task_id)


def _post(item: OpenPR, provider: object, attempt: int, diff: str, request: str) -> None:
    provider.post_comment(item.number, format_revision(attempt, diff, request))  # type: ignore[attr-defined]


class CheckUnavailable(RuntimeError):
    """A check could not start. This is not a revision round."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class WorkerStartFailed(RuntimeError):
    """The worker exited before its first step. This is not a revision round."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _revise(item: OpenPR, feedback: str, provider: object, hooks: PollHooks, *, attempt: int) -> str | None:
    """Push one revision. None means the worker made no commit."""
    item.pending_revision = True
    hooks.note("pr.revision_started", item.task_id, {"attempt": attempt, "number": item.number})
    try:
        new_sha = hooks.revise(item.task_id, feedback)
    except WorkerStartFailed:
        item.pending_revision = False
        raise
    except CheckUnavailable as exc:
        item.pending_revision = False
        item.status = "needs_review"
        item.unresolved = True
        item.last_failure = exc.reason[:1800]
        hooks.note("check.unavailable", item.task_id, {"task": item.task_id, "reason": exc.reason[:300]})
        raise
    item.pending_revision = False
    if not new_sha:
        hooks.note(
            "pr.revision_failed",
            item.task_id,
            {"task": item.task_id, "reason": "revision made no commit"},
        )
        return None
    item.head_sha = new_sha
    item.checks = "pending"
    item.unresolved = False
    hooks.note("pr.revision_pushed", item.task_id, {"attempt": attempt, "sha": new_sha, "number": item.number})
    return new_sha


def _judge(item: OpenPR, body: str, diff: str, hooks: PollHooks) -> tuple[str, str]:
    if hooks.judge is None:
        return "unsatisfied", "no reviewer"
    verdict, reason = hooks.judge(item.task_id, body, diff)
    if verdict not in {"satisfied", "unsatisfied"}:
        verdict, reason = parse_acceptance(verdict)
    reason = public_text(reason)[:120] or "does not match the request"
    if body and body in reason:
        reason = "matches the request" if verdict == "satisfied" else "does not match the request"
    return verdict, reason


def _satisfy(item: OpenPR, tracked: TrackedComment, provider: object, table: ReactionTable, hooks: PollHooks) -> None:
    """Revise until the reviewer is satisfied or the comment's rounds are spent."""
    limit = resolve(Event.pr_comment, 1, table).max_attempts
    while tracked.status != "satisfied" and tracked.rounds_used < limit:
        feedback = revision_request(tracked.body, reason=tracked.reason)
        try:
            sha = _revise(item, feedback, provider, hooks, attempt=tracked.rounds_used + 1)
        except CheckUnavailable:
            return
        except WorkerStartFailed as exc:
            tracked.status = "needs_human"
            tracked.reason = exc.reason[:1800]
            item.status = "needs_review"
            item.unresolved = True
            item.pending_revision = False
            item.last_failure = tracked.reason
            hooks.note(
                "worker.start_failed",
                item.task_id,
                {"task": item.task_id, "reason": exc.reason[:300]},
            )
            return
        tracked.rounds_used += 1
        if sha is None:
            tracked.status = "unsatisfied"
            tracked.reason = "revision made no commit"
            continue
        diff = _diff(item, hooks)
        verdict, reason = _judge(item, tracked.body, diff, hooks)
        tracked.reason = reason
        tracked.status = verdict
        request = "satisfied" if verdict == "satisfied" else f"not satisfied ({reason[:120]})"
        _post(item, provider, tracked.rounds_used, diff, request)
    if tracked.status != "satisfied":
        tracked.status = "needs_human"
        item.status = "needs_review"
        item.unresolved = True
        item.last_failure = tracked.reason[:1800]
        hooks.note(
            "pr.checks",
            item.task_id,
            {"status": "needs_review", "number": item.number, "reason": tracked.reason[:300]},
        )


def _on_comment(item: OpenPR, comment: object, provider: object, table: ReactionTable, hooks: PollHooks) -> None:
    body = str(getattr(comment, "body", ""))
    comment_id = int(getattr(comment, "id", 0))
    existing = next((row for row in item.comments if row.id == comment_id), None)
    if existing is not None and existing.status in {"needs_human", "satisfied"}:
        return
    attempt = _attempt(item, "pr_comment")
    decision = resolve(Event.pr_comment, attempt, table)
    hooks.note(
        "pr.comment_seen",
        item.task_id,
        {"comment_id": comment_id, "number": item.number, "attempt": attempt},
    )
    hooks.note("pr.comment", item.task_id, {"author_trusted": True, "number": item.number, "attempt": attempt})
    if existing is None:
        tracked = TrackedComment(id=comment_id, body=body)
        item.comments.append(tracked)
    else:
        existing.body = body
        tracked = existing
    item.unresolved = True
    if decision.action != Action.send_to_agent:
        tracked.status = "needs_human"
        _exhausted(item, "round limit reached", hooks)
        return
    _satisfy(item, tracked, provider, table, hooks)


def _on_checks(item: OpenPR, provider: object, table: ReactionTable, hooks: PollHooks) -> None:
    attempt = _attempt(item, "checks_failed")
    decision = resolve(Event.checks_failed, attempt, table)
    summary = provider.get_failed_check_summary(item.number)  # type: ignore[attr-defined]
    item.checks = "failing"
    hooks.note("pr.checks", item.task_id, {"checks": "failing", "number": item.number, "attempt": attempt})
    if decision.action != Action.send_to_agent:
        item.checks = "revising"
        _exhausted(item, summary, hooks)
        return
    item.checks = "revising"
    try:
        sha = _revise(item, delimited_feedback(summary or "a check failed"), provider, hooks, attempt=attempt)
    except CheckUnavailable:
        return
    if sha is None:
        return
    _post(item, provider, attempt, _diff(item, hooks), "not satisfied (a check failed)")


def _on_conflict(item: OpenPR, provider: object, table: ReactionTable, hooks: PollHooks) -> None:
    if item.checks == "conflict":
        return
    attempt = _attempt(item, "merge_conflict")
    decision = resolve(Event.merge_conflict, attempt, table)
    hooks.note("pr.conflict", item.task_id, {"number": item.number, "attempt": attempt})
    if decision.action != Action.send_to_agent:
        item.checks = "conflict"
        _exhausted(item, "merge conflict", hooks)
        return
    item.checks = "conflict"
    hooks.note(
        "pr.revision_started", item.task_id, {"attempt": attempt, "number": item.number, "reason": "merge_conflict"}
    )
    new_sha = hooks.resolve_conflict(item.task_id)
    if not new_sha:
        hooks.note(
            "pr.revision_failed",
            item.task_id,
            {"task": item.task_id, "reason": "revision made no commit"},
        )
        return
    item.head_sha = new_sha
    item.checks = "pending"
    _post(item, provider, attempt, _diff(item, hooks), "satisfied")
    hooks.note("pr.revision_pushed", item.task_id, {"attempt": attempt, "sha": new_sha, "number": item.number})


def _comments_block(item: OpenPR) -> bool:
    return item.pending_revision or any(comment.status != "satisfied" for comment in item.comments)


def _on_green(item: OpenPR, provider: object, table: ReactionTable, auto_merge: bool, hooks: PollHooks) -> None:
    item.checks = "passing"
    hooks.note("pr.checks_green", item.task_id, {"number": item.number, "checks": "passing"})
    if _comments_block(item) or item.unresolved:
        return
    decision = resolve(Event.approved_green, 1, table)
    action = decision.action
    if Event.approved_green.value in table.defaults and action == Action.ignore:
        action = Action.notify
    item.status = "approved_green"
    hooks.note(
        "pr.checks",
        item.task_id,
        {"checks": "passing", "approved": True, "action": action.value, "number": item.number},
    )
    if auto_merge:
        provider.merge(item.number)  # type: ignore[attr-defined]


def awaiting(opened: list[OpenPR]) -> bool:
    return any(item.status not in _DONE for item in opened)


def pr_comment_body_has_marker(body: str) -> bool:
    return MARKER in body
