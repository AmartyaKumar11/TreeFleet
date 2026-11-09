"""Pull request values. Nothing here talks to GitHub."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CheckRun:
    name: str
    status: str
    conclusion: str = ""


@dataclass(frozen=True)
class PR:
    number: int
    url: str
    head: str
    base: str
    head_sha: str


@dataclass(frozen=True)
class PRState:
    number: int
    state: str
    mergeable: str
    check_runs: tuple[CheckRun, ...]
    head_sha: str
    url: str


@dataclass(frozen=True)
class Comment:
    id: int
    author: str
    body: str


@dataclass
class TrackedComment:
    """One trusted comment. The body stays in memory and is not written to the run record."""

    id: int
    body: str
    status: str = "open"
    rounds_used: int = 0
    reason: str = ""


@dataclass
class OpenPR:
    """One PR this run opened. The poller never tracks anything else."""

    task_id: str
    number: int
    url: str
    head: str
    head_sha: str
    last_comment_id: int = 0
    rounds: dict[str, int] = field(default_factory=dict)
    status: str = "open"
    checks: str = "pending"
    unresolved: bool = False
    last_failure: str = ""
    comments: list[TrackedComment] = field(default_factory=list)
    pending_revision: bool = False
