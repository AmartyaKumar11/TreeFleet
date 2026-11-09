"""Push and comment rules. Enforced here, not by the model."""

from __future__ import annotations

import re
from pathlib import Path

from treefleet.guards.input import deterministic_reasons
from treefleet.guards.text import redact
from treefleet.pr.models import Comment

MARKER = "<!-- treefleet -->"
_CAP = 1800
_WIN_PATH = re.compile(r"[A-Za-z]:\\[^\s]+")
_POSIX_ABS = re.compile(r"(?<![:\w])/[\w./-]{2,}")
_ENV_VALUE = re.compile(r"\b[A-Z][A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)\b\s*=\s*\S+")


class PrSafetyError(RuntimeError):
    """A push or a comment broke a safety rule."""


def urls_match(remote: str, repo: str) -> bool:
    """True when origin is exactly the configured owner/name, ignoring .git and ssh form."""

    def norm(url: str) -> str:
        text = url.strip().rstrip("/")
        if text.endswith(".git"):
            text = text[:-4]
        if text.startswith("git@github.com:"):
            text = "https://github.com/" + text.split(":", 1)[1]
        if text.startswith("ssh://git@github.com/"):
            text = "https://github.com/" + text.split("github.com/", 1)[1]
        return text.lower()

    return norm(remote) == norm(f"https://github.com/{repo}")


def task_branch(run_id: str, task_id: str) -> str:
    if not run_id or not task_id or "/" in run_id or "/" in task_id:
        raise PrSafetyError("refusing a branch outside this run")
    return f"tf/{run_id}/{task_id}"


def assert_our_branch(run_id: str, branch: str, *, base: str) -> str:
    """Accept only tf/<this run>/<task>. main and the base branch never match."""
    expected_prefix = f"tf/{run_id}/"
    task = branch[len(expected_prefix) :] if branch.startswith(expected_prefix) else ""
    if not task or "/" in task or branch in {base, "main", "master"}:
        raise PrSafetyError("refusing a branch outside this run")
    return branch


def push_refspec(run_id: str, task_id: str, *, remote_url: str, configured_repo: str, base: str) -> list[str]:
    """The only push this package builds. No force, no base, no main."""
    if not urls_match(remote_url, configured_repo):
        raise PrSafetyError("origin does not match pr.repo")
    branch = assert_our_branch(run_id, task_branch(run_id, task_id), base=base)
    return ["push", "origin", f"{branch}:{branch}"]


def push_task(
    repo: Path,
    run_id: str,
    task_id: str,
    *,
    remote_url: str,
    configured_repo: str,
    base: str,
    run_git: object,
) -> None:
    args = push_refspec(run_id, task_id, remote_url=remote_url, configured_repo=configured_repo, base=base)
    proc = run_git(args, cwd=repo)  # type: ignore[operator]
    if getattr(proc, "returncode", 1) != 0:
        raise PrSafetyError("git push failed")


def public_text(text: str) -> str:
    """Drop tokens, env-looking strings, and absolute paths. Never a raw diff."""
    redacted, _count = redact(text)
    cleaned = _ENV_VALUE.sub("[redacted]", redacted)
    cleaned = _WIN_PATH.sub("[path]", cleaned)
    cleaned = _POSIX_ABS.sub("[path]", cleaned)
    return cleaned[:_CAP]


def pr_body(*, goal: str, scope: str, verdict: str, checks: str, run_id: str) -> str:
    lines = [
        f"Goal: {public_text(goal)}",
        f"Scope: {public_text(scope)}",
        f"Review: {public_text(verdict)}",
        f"Checks: {public_text(checks)}",
        f"Run: {run_id}",
    ]
    return "\n".join(lines)


def revision_comment(round_no: int, line: str, *, diff: str = "", request: str = "satisfied") -> str:
    """Public comment. `line` is the request and is never copied into the body."""
    from treefleet.pr.accept import format_revision

    del line
    return format_revision(round_no, diff, request)


def comment_kind(comment: Comment, trusted: set[str]) -> str:
    if MARKER in comment.body:
        return "own"
    if comment.author not in trusted:
        return "untrusted"
    return "trusted"


def delimited_feedback(body: str) -> str:
    """Untrusted text is data. The input guard runs, and the text is never a command."""
    text = public_text(body)
    reasons = deterministic_reasons(text)
    note = f"guard: {', '.join(reasons)}\n" if reasons else ""
    return f"<untrusted-data>\n{note}{text}\n</untrusted-data>"
