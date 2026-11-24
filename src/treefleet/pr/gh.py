"""gh subprocess. Argument lists only. --repo is always explicit."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable, Sequence
from typing import Any

from treefleet.guards.text import redact
from treefleet.pr.models import PR, CheckRun, Comment, PRState

_BACKOFF_S = (1.0, 2.0)
_OPEN_BACKOFF_S = (5.0, 20.0)
_GH_TIMEOUT_S = 120
_OPEN_RETRIES = 2  # retries after the first attempt (3 tries total)
Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]
RetryHook = Callable[[int, str], None]

_PERMANENT_MARKERS = (
    "http 422",
    "http 401",
    "http 403",
    "status 422",
    "status 401",
    "status 403",
    "already exists",
    "no commits between",
    "not found",
    "could not find",
    "branch not found",
    "head can't be resolved",
    "head cannot be resolved",
    "does not exist",
    "pull request create failed",  # avoid loops on our own messages
)


class GhError(RuntimeError):
    """gh failed. The message is redacted and short."""


def _clip(text: str) -> str:
    redacted, _count = redact(text)
    return redacted.replace("\n", " ")[:300]


def _blob(stderr: str = "", stdout: str = "", exc: BaseException | None = None) -> str:
    parts = [stderr or "", stdout or ""]
    if exc is not None:
        parts.append(str(exc))
    return "\n".join(parts).lower()


def reason_class_for(text: str, *, timed_out: bool = False) -> str:
    """Classify a failure for events and retry policy. Never includes secrets."""
    if timed_out or isinstance(text, str) and "timeoutexpired" in text.lower():
        return "timeout"
    blob = text.lower()
    if any(m in blob for m in _PERMANENT_MARKERS):
        return "permanent"
    if "429" in blob or "rate limit" in blob:
        return "http_429"
    if any(code in blob for code in ("500", "502", "503", "504")) or "http 5" in blob:
        return "http_5xx"
    if any(
        marker in blob
        for marker in (
            "connection reset",
            "connection refused",
            "connection attempt failed",
            "failed to respond",
            "wsarecv",
            "eof",
            "tls",
            "ssl",
            "timeout",
            "timed out",
        )
    ):
        return "network"
    return "unknown"


def is_transient_open_failure(text: str, *, timed_out: bool = False) -> bool:
    """True only for failures safe to retry on `gh pr create`."""
    kind = reason_class_for(text, timed_out=timed_out)
    return kind in {"timeout", "network", "http_5xx", "http_429"}


def _transient(proc: subprocess.CompletedProcess[str]) -> bool:
    """Used by non-create `_call` retries (api/view/etc.)."""
    return is_transient_open_failure(_blob(proc.stderr or "", proc.stdout or ""))


class GhProvider:
    def __init__(self, repo: str, *, runner: Runner | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        if "/" not in repo:
            raise GhError("gh repo must be owner/name")
        self.repo = repo
        self._runner = runner or _default_runner
        self._sleep = sleep

    def _invoke(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        command = ["gh", *args]
        if len(command) > 1 and command[1] == "api":
            command = _without_flag(command, "--repo")
        elif "--repo" not in command and not any(item.startswith("--repo=") for item in command):
            command.extend(["--repo", self.repo])
        return self._runner(list(command))

    def _call(self, args: Sequence[str], *, attempts: int = 3) -> subprocess.CompletedProcess[str]:
        last = ""
        tries = max(1, attempts)
        for attempt in range(tries):
            try:
                proc = self._invoke(args)
            except (TimeoutError, OSError, subprocess.TimeoutExpired) as exc:
                last = _clip(str(exc))
                if attempt < tries - 1 and is_transient_open_failure(last, timed_out=True):
                    self._sleep(_BACKOFF_S[min(attempt, len(_BACKOFF_S) - 1)])
                    continue
                raise GhError(last or "gh timed out") from exc
            if proc.returncode == 0:
                return proc
            last = _clip(proc.stderr or proc.stdout)
            if attempt < tries - 1 and _transient(proc):
                self._sleep(_BACKOFF_S[min(attempt, len(_BACKOFF_S) - 1)])
                continue
            raise GhError(last or "gh failed")
        raise GhError(last or "gh failed")

    def user(self) -> str:
        proc = self._call(["api", "user", "--jq", ".login"])
        return proc.stdout.strip()

    def find_open_pr(self, head: str, base: str) -> PR | None:
        """Return an open PR for this head/base if one exists."""
        try:
            proc = self._call(
                [
                    "pr",
                    "list",
                    "--repo",
                    self.repo,
                    "--head",
                    head,
                    "--base",
                    base,
                    "--state",
                    "open",
                    "--json",
                    "number,url",
                ],
                attempts=3,
            )
        except GhError:
            return None
        try:
            rows = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError:
            return None
        if not isinstance(rows, list) or not rows:
            return None
        first = rows[0]
        if not isinstance(first, dict):
            return None
        number = int(first.get("number") or 0)
        url = str(first.get("url") or "")
        if number <= 0 or not url.startswith("http"):
            return None
        try:
            state = self.get_pr(number)
            head_sha = state.head_sha
        except GhError:
            head_sha = ""
        return PR(number=number, url=url, head=head, base=base, head_sha=head_sha)

    def open_pr(
        self,
        head: str,
        base: str,
        title: str,
        body: str,
        on_retry: RetryHook | None = None,
    ) -> PR:
        """Create a PR. Retries transient failures twice (5s, 20s) and reuses an existing open PR."""
        last_error = "gh pr create failed"
        last_class = "unknown"
        create_args = [
            "pr",
            "create",
            "--repo",
            self.repo,
            "--base",
            base,
            "--head",
            head,
            "--title",
            title,
            "--body",
            body,
        ]
        for attempt in range(_OPEN_RETRIES + 1):
            if attempt > 0:
                if on_retry is not None:
                    on_retry(attempt, last_class)
                existing = self.find_open_pr(head, base)
                if existing is not None:
                    return existing
                self._sleep(_OPEN_BACKOFF_S[attempt - 1])
            try:
                proc = self._invoke(create_args)
            except (TimeoutError, OSError, subprocess.TimeoutExpired) as exc:
                last_error = _clip(str(exc)) or "gh timed out"
                last_class = reason_class_for(last_error, timed_out=True)
                if attempt >= _OPEN_RETRIES or not is_transient_open_failure(last_error, timed_out=True):
                    raise GhError(last_error) from exc
                continue
            if proc.returncode == 0:
                url = next(
                    (line.strip() for line in proc.stdout.splitlines() if line.strip().startswith("http")),
                    "",
                )
                number = int(url.rstrip("/").split("/")[-1]) if url.rsplit("/", 1)[-1].isdigit() else 0
                if number <= 0:
                    raise GhError("gh pr create did not return a pull request url")
                state = self.get_pr(number)
                return PR(number=number, url=url, head=head, base=base, head_sha=state.head_sha)
            last_error = _clip(proc.stderr or proc.stdout) or "gh failed"
            last_class = reason_class_for(last_error)
            if attempt >= _OPEN_RETRIES or not is_transient_open_failure(last_error):
                raise GhError(last_error)
        raise GhError(last_error)

    def get_pr(self, number: int) -> PRState:
        proc = self._call(
            [
                "pr",
                "view",
                str(number),
                "--repo",
                self.repo,
                "--json",
                "state,mergeable,statusCheckRollup,headRefOid,url,mergedAt",
            ]
        )
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise GhError("gh pr view returned non-json") from exc
        return _state(number, payload if isinstance(payload, dict) else {})

    def list_comments(self, number: int, since_id: int) -> list[Comment]:
        proc = self._call(
            [
                "api",
                "--paginate",
                f"repos/{self.repo}/issues/{number}/comments",
            ]
        )
        try:
            payload = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise GhError("gh api comments returned non-json") from exc
        rows = payload if isinstance(payload, list) else []
        found: list[Comment] = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            comment_id = int(item.get("id") or 0)
            if comment_id <= since_id:
                continue
            user_raw = item.get("user")
            user = user_raw if isinstance(user_raw, dict) else {}
            found.append(
                Comment(
                    id=comment_id,
                    author=str(user.get("login") or ""),
                    body=str(item.get("body") or ""),
                    created_at=str(item.get("created_at") or ""),
                )
            )
        return found

    def post_comment(self, number: int, body: str) -> Comment:
        proc = self._call(["pr", "comment", str(number), "--repo", self.repo, "--body", body])
        url = next((line.strip() for line in proc.stdout.splitlines() if "://" in line), "")
        return Comment(id=0, author="", body=url)

    def get_failed_check_summary(self, number: int) -> str:
        state = self.get_pr(number)
        lines = [
            f"{item.name} {item.conclusion or item.status}"
            for item in state.check_runs
            if item.conclusion not in {"", "success", "skipped", "neutral"}
        ]
        redacted, _count = redact("\n".join(lines))
        return redacted[:1800]

    def merge(self, number: int) -> None:
        self._call(["pr", "merge", str(number), "--repo", self.repo, "--squash"])


def _without_flag(args: list[str], flag: str) -> list[str]:
    kept: list[str] = []
    skip = False
    for item in args:
        if skip:
            skip = False
            continue
        if item == flag:
            skip = True
            continue
        if item.startswith(flag + "="):
            continue
        kept.append(item)
    return kept


def _default_runner(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_GH_TIMEOUT_S,
        check=False,
    )


def _state(number: int, payload: dict[str, Any]) -> PRState:
    rollup = payload.get("statusCheckRollup")
    checks: list[CheckRun] = []
    if isinstance(rollup, list):
        for item in rollup:
            if not isinstance(item, dict):
                continue
            checks.append(
                CheckRun(
                    name=str(item.get("name") or item.get("context") or "check"),
                    status=str(item.get("status") or "").lower(),
                    conclusion=str(item.get("conclusion") or item.get("state") or "").lower(),
                )
            )
    merged = bool(payload.get("mergedAt"))
    raw_state = str(payload.get("state") or "open").lower()
    state = "merged" if merged else raw_state
    return PRState(
        number=number,
        state=state,
        mergeable=str(payload.get("mergeable") or "UNKNOWN"),
        check_runs=tuple(checks),
        head_sha=str(payload.get("headRefOid") or ""),
        url=str(payload.get("url") or ""),
    )
