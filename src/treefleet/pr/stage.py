"""Open and watch this run's pull requests. Shared code: no agent framework imports."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from treefleet.pr.config import PrSettings
from treefleet.pr.loop import PollHooks, awaiting, poll_once
from treefleet.pr.models import OpenPR
from treefleet.pr.preflight import external_hook_block
from treefleet.pr.safety import PrSafetyError, pr_body, push_refspec, task_branch

Push = Callable[[str], str]


def git_push(repo: Path, run_id: str, task_id: str, settings: PrSettings) -> str:
    """Push exactly tf/<run>/<task>. The remote URL is checked and never returned."""
    from treefleet.git.worktrees import run_git

    remote = run_git(["remote", "get-url", "origin"], cwd=repo)
    if remote.returncode != 0:
        raise PrSafetyError("origin does not match pr.repo")
    args = push_refspec(
        run_id,
        task_id,
        remote_url=remote.stdout.strip(),
        configured_repo=settings.repo,
        base=settings.base,
    )
    pushed = run_git(args, cwd=repo)
    if pushed.returncode != 0:
        raise PrSafetyError("git push failed")
    branch = task_branch(run_id, task_id)
    sha = run_git(["rev-parse", branch], cwd=repo)
    if sha.returncode != 0 or not sha.stdout.strip():
        raise PrSafetyError("git push failed")
    return sha.stdout.strip()


def drive(
    tasks: list[dict[str, str]],
    provider: object,
    hooks: PollHooks,
    settings: PrSettings,
    *,
    run_id: str,
    remote_url: str,
    push: Push,
    login: str,
    hook_hosts: list[str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    deadline: float | None = None,
) -> tuple[list[OpenPR], str]:
    """Push, open, and poll. Returns the pull requests and done or timed_out."""
    if not settings.enabled:
        return [], "done"
    blocked = external_hook_block(settings, list(hook_hosts or []))
    if blocked:
        raise PrSafetyError(blocked)
    trusted = set(settings.trusted_commenters) or ({login} if login else set())
    opened: list[OpenPR] = []
    for task in tasks:
        task_id = task["id"]
        branch = task_branch(run_id, task_id)
        # Build the refspec even when push is injected, so a bad remote still aborts.
        push_refspec(run_id, task_id, remote_url=remote_url, configured_repo=settings.repo, base=settings.base)
        sha = push(task_id)
        body = pr_body(
            goal=task.get("goal", ""),
            scope=task.get("scope", ""),
            verdict=task.get("verdict", ""),
            checks=task.get("checks", ""),
            run_id=run_id,
        )
        pr = provider.open_pr(branch, settings.base, task.get("title") or task_id, body)  # type: ignore[attr-defined]
        item = OpenPR(
            task_id=task_id,
            number=pr.number,
            url=pr.url,
            head=branch,
            head_sha=sha or pr.head_sha,
        )
        opened.append(item)
        hooks.note("pr.opened", task_id, {"number": pr.number, "url": pr.url, "sha": item.head_sha})
    outcome = poll_until(
        opened,
        provider,
        hooks,
        settings,
        trusted=trusted,
        sleep=sleep,
        now=now,
        deadline=deadline,
    )
    return opened, outcome


def poll_until(
    opened: list[OpenPR],
    provider: object,
    hooks: PollHooks,
    settings: PrSettings,
    *,
    trusted: set[str] | None = None,
    login: str = "",
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    deadline: float | None = None,
) -> str:
    """Keep polling until every pull request is merged or closed, or the deadline passes."""
    known = set(settings.trusted_commenters) if trusted is None else trusted
    if not known and login:
        known = {login}
    end = now() + settings.max_wait_minutes * 60 if deadline is None else deadline
    while awaiting(opened):
        poll_once(opened, provider, trusted=known, auto_merge=settings.auto_merge, hooks=hooks)
        if not awaiting(opened):
            return "done"
        if now() >= end:
            return "timed_out"
        sleep(settings.poll_seconds)
    return "done"


def status_lines(tasks: list[dict[str, object]]) -> list[str]:
    """One row per task. No comment text and no tokens."""
    rows: list[str] = []
    for task in tasks:
        number = task.get("pr_number") or "-"
        rows.append(
            f"{task.get('task_id')} pr={number} status={task.get('pr_status') or task.get('status') or '-'} "
            f"checks={task.get('pr_checks') or '-'}"
        )
    return rows


def merge_task(provider: object, number: int) -> None:
    """The CLI merge command. The poller does not call this."""
    provider.merge(number)  # type: ignore[attr-defined]
