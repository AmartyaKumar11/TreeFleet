"""Poll only the pull requests this run opened."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from treefleet.guards.text import redact
from treefleet.pr.models import CheckRun, OpenPR, PRState
from treefleet.pr.safety import MARKER, comment_kind, delimited_feedback, public_text, revision_comment
from treefleet.reactions import Action, Event, ReactionTable, default_table, resolve

_POLL_BACKOFF_S = (5.0, 15.0, 45.0)
_POLL_FAILS = 5

_DONE = frozenset({"merged", "cancelled"})
_FAIL = frozenset({"failure", "timed_out", "cancelled", "action_required"})
_GREEN = frozenset({"success", "skipped", "neutral"})


@dataclass
class PollHooks:
    """The stage supplies these. Tests pass plain functions. merge is not one of them."""

    revise: Callable[[str, str], str | None]
    resolve_conflict: Callable[[str], str | None]
    emit: Callable[[str, str, dict[str, object]], None]
    events: list[tuple[str, str, dict[str, object]]] = field(default_factory=list)

    def note(self, kind: str, task_id: str, payload: dict[str, object]) -> None:
        self.events.append((kind, task_id, payload))
        self.emit(kind, task_id, payload)


def check_label(state: PRState) -> str:
    runs = state.check_runs
    if not runs:
        return "pending"
    if any(item.conclusion in _FAIL for item in runs):
        return "failing"
    if _green(runs):
        return "passing"
    return "pending"


def _green(runs: tuple[CheckRun, ...]) -> bool:
    return bool(runs) and all(item.conclusion in _GREEN for item in runs)


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
        _on_comment(item, comment.body, provider, policy, hooks)
        acted = True
    if acted or item.status in _DONE or item.status == "needs_review":
        return
    if state.mergeable == "CONFLICTING":
        _on_conflict(item, provider, policy, hooks)
        return
    label = (
        "failing"
        if any(run.conclusion in _FAIL for run in state.check_runs) and state.head_sha == item.head_sha
        else check_label(state)
    )
    if label == "failing" and state.head_sha == item.head_sha and item.checks != "revising":
        _on_checks(item, provider, policy, hooks)
        return
    if (
        _green(state.check_runs)
        and state.mergeable != "CONFLICTING"
        and not item.unresolved
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


def _revise(item: OpenPR, feedback: str, provider: object, hooks: PollHooks, *, attempt: int) -> None:
    hooks.note("pr.revision_started", item.task_id, {"attempt": attempt, "number": item.number})
    new_sha = hooks.revise(item.task_id, feedback)
    if not new_sha:
        return
    item.head_sha = new_sha
    item.checks = "pending"
    item.unresolved = False
    provider.post_comment(item.number, revision_comment(attempt, feedback))  # type: ignore[attr-defined]
    hooks.note("pr.revision_pushed", item.task_id, {"attempt": attempt, "sha": new_sha, "number": item.number})


def _on_comment(item: OpenPR, body: str, provider: object, table: ReactionTable, hooks: PollHooks) -> None:
    attempt = _attempt(item, "pr_comment")
    decision = resolve(Event.pr_comment, attempt, table)
    hooks.note("pr.comment", item.task_id, {"author_trusted": True, "number": item.number, "attempt": attempt})
    item.unresolved = True
    if decision.action != Action.send_to_agent:
        _exhausted(item, body, hooks)
        return
    _revise(item, delimited_feedback(body), provider, hooks, attempt=attempt)


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
    _revise(item, delimited_feedback(summary or "a check failed"), provider, hooks, attempt=attempt)


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
        return
    item.head_sha = new_sha
    item.checks = "pending"
    provider.post_comment(item.number, revision_comment(attempt, "merged the base and resolved the conflict"))  # type: ignore[attr-defined]
    hooks.note("pr.revision_pushed", item.task_id, {"attempt": attempt, "sha": new_sha, "number": item.number})


def _on_green(item: OpenPR, provider: object, table: ReactionTable, auto_merge: bool, hooks: PollHooks) -> None:
    decision = resolve(Event.approved_green, 1, table)
    action = decision.action
    if Event.approved_green.value in table.defaults and action == Action.ignore:
        action = Action.notify
    item.status = "approved_green"
    item.checks = "passing"
    hooks.note("pr.checks", item.task_id, {"checks": "passing", "action": action.value, "number": item.number})
    if auto_merge:
        provider.merge(item.number)  # type: ignore[attr-defined]


def awaiting(opened: list[OpenPR]) -> bool:
    return any(item.status not in _DONE for item in opened)


def pr_comment_body_has_marker(body: str) -> bool:
    return MARKER in body
