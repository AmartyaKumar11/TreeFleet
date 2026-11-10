"""The only place a PollHooks value is built. The stage and `pr watch` both call it."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from treefleet.pr.config import PrSettings
from treefleet.pr.loop import AcceptanceUnavailable, PollHooks
from treefleet.pr.trust import trusted_logins


def task_diff(worktree: Path) -> str:
    """The last commit's patch. Empty when the worktree has no parent commit."""
    from treefleet.git.worktrees import run_git

    if not worktree.is_dir():
        return ""
    proc = run_git(["diff", "HEAD~1"], cwd=worktree)
    if proc.returncode != 0:
        return ""
    return proc.stdout


class _Reviewer:
    """Always callable. A missing judge fails closed instead of voting unsatisfied."""

    def __init__(self, judge: Callable[[str, str, str], tuple[str, str]] | None) -> None:
        self._judge = judge
        self.blocked_reason = "reviewer unavailable" if judge is None else ""

    def __call__(self, task_id: str, comment: str, diff: str) -> tuple[str, str]:
        if self._judge is None:
            raise AcceptanceUnavailable(self.blocked_reason or "reviewer unavailable")
        try:
            return self._judge(task_id, comment, diff)
        except AcceptanceUnavailable:
            raise
        except Exception as exc:
            raise AcceptanceUnavailable("reviewer unavailable") from exc


def build_poll_hooks(
    *,
    revise: Callable[[str, str], str | None],
    resolve_conflict: Callable[[str], str | None],
    judge: Callable[[str, str, str], tuple[str, str]] | None,
    emit: Callable[[str, str, dict[str, object]], None],
    diff: Callable[[str], str] | None,
    login: str = "",
    provider: object | None = None,
    settings: PrSettings | None = None,
) -> PollHooks:
    """Revise, resolve, judge, emit, diff, and the trusted login, in one place."""
    trusted: set[str] = set()
    problem = ""
    resolved = login
    checked = provider is not None and settings is not None
    if checked:
        assert provider is not None and settings is not None
        trusted, problem = trusted_logins(
            settings,
            provider,
            fallback="" if callable(getattr(provider, "user", None)) else login,
        )
        user = getattr(provider, "user", None)
        if callable(user):
            try:
                resolved = str(user() or "")
            except Exception:
                resolved = ""
        elif login:
            resolved = login
    return PollHooks(
        revise=revise,
        resolve_conflict=resolve_conflict,
        emit=emit,
        judge=_Reviewer(judge),
        diff=diff if diff is not None else (lambda _task: ""),
        login=resolved,
        trusted=trusted,
        trust_problem=problem,
        trust_checked=checked,
    )


def hooks_for_stage(
    *,
    revise: Callable[[str, str], str | None],
    resolve_conflict: Callable[[str], str | None],
    judge: Callable[[str, str, str], tuple[str, str]] | None,
    emit: Callable[[str, str, dict[str, object]], None],
    diff: Callable[[str], str] | None,
    login: str = "",
    provider: object | None = None,
    settings: PrSettings | None = None,
) -> PollHooks:
    """In-run poller entry. Same builder as `pr watch`."""
    return build_poll_hooks(
        revise=revise,
        resolve_conflict=resolve_conflict,
        judge=judge,
        emit=emit,
        diff=diff,
        login=login,
        provider=provider,
        settings=settings,
    )


def hooks_for_watch(
    *,
    revise: Callable[[str, str], str | None],
    resolve_conflict: Callable[[str], str | None],
    judge: Callable[[str, str, str], tuple[str, str]] | None,
    emit: Callable[[str, str, dict[str, object]], None],
    diff: Callable[[str], str] | None,
    login: str = "",
    provider: object | None = None,
    settings: PrSettings | None = None,
) -> PollHooks:
    """`pr watch` entry. Same builder as the in-run poller."""
    return build_poll_hooks(
        revise=revise,
        resolve_conflict=resolve_conflict,
        judge=judge,
        emit=emit,
        diff=diff,
        login=login,
        provider=provider,
        settings=settings,
    )
