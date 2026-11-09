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
Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


class GhError(RuntimeError):
    """gh failed. The message is redacted and short."""


def _transient(proc: subprocess.CompletedProcess[str]) -> bool:
    text = f"{proc.stderr}\n{proc.stdout}".lower()
    if "rate limit" in text or "http 429" in text or "http 502" in text or "http 503" in text:
        return True
    return proc.returncode != 0 and any(code in text for code in ("503", "502", "429"))


def _clip(text: str) -> str:
    redacted, _count = redact(text)
    return redacted.replace("\n", " ")[:300]


class GhProvider:
    def __init__(self, repo: str, *, runner: Runner | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        if "/" not in repo:
            raise GhError("gh repo must be owner/name")
        self.repo = repo
        self._runner = runner or _default_runner
        self._sleep = sleep

    def _call(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        command = ["gh", *args]
        if "--repo" not in command:
            command.extend(["--repo", self.repo])
        last = ""
        for attempt in range(3):
            try:
                proc = self._runner(list(command))
            except (TimeoutError, OSError) as exc:
                last = _clip(str(exc))
                if attempt < 2:
                    self._sleep(_BACKOFF_S[attempt])
                    continue
                raise GhError(last) from exc
            if proc.returncode == 0:
                return proc
            last = _clip(proc.stderr or proc.stdout)
            if _transient(proc) and attempt < 2:
                self._sleep(_BACKOFF_S[attempt])
                continue
            raise GhError(last or "gh failed")
        raise GhError(last or "gh failed")

    def open_pr(self, head: str, base: str, title: str, body: str) -> PR:
        proc = self._call(
            ["pr", "create", "--repo", self.repo, "--base", base, "--head", head, "--title", title, "--body", body]
        )
        url = next((line.strip() for line in proc.stdout.splitlines() if line.strip().startswith("http")), "")
        number = int(url.rstrip("/").split("/")[-1]) if url.rsplit("/", 1)[-1].isdigit() else 0
        if number <= 0:
            raise GhError("gh pr create did not return a pull request url")
        state = self.get_pr(number)
        return PR(number=number, url=url, head=head, base=base, head_sha=state.head_sha)

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
                "--repo",
                self.repo,
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
            found.append(Comment(id=comment_id, author=str(user.get("login") or ""), body=str(item.get("body") or "")))
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


def _default_runner(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
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
                    status=str(item.get("status") or ""),
                    conclusion=str(item.get("conclusion") or ""),
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
