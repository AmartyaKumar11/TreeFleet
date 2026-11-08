"""Isolation proofs from git and the process tree. Nothing here trusts a worker's report."""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import psutil

from treefleet.contracts import CwdAttempt, CwdProcessView, FileChange, IsolationProof, MainCheckoutProof
from treefleet.git.canary import CANARY_NAME
from treefleet.git.worktrees import GitError, Worktree, _same, branch_name, git_out, list_worktrees, run_git


@dataclass(frozen=True)
class MainSnapshot:
    status: tuple[str, ...]
    head: str


@dataclass(frozen=True)
class SiblingSnapshot:
    path: str
    head: str
    status: tuple[str, ...]


@dataclass(frozen=True)
class SiblingProof:
    ok: bool
    offending: tuple[str, ...]


def _match(path: str, pattern: str) -> bool:
    rel = path.replace("\\", "/")
    pat = pattern.replace("\\", "/")
    if PurePosixPath(rel).match(pat):
        return True
    if any(char in pat for char in "*?["):
        return False
    prefix = pat.rstrip("/")
    return rel == prefix or rel.startswith(prefix + "/")


def in_scope(path: str, allowed: list[str], denied: list[str]) -> bool:
    if any(_match(path, pattern) for pattern in denied):
        return False
    return any(_match(path, pattern) for pattern in allowed)


def diff_stats(worktree: Path, base_sha: str) -> list[FileChange]:
    """Committed and uncommitted tracked changes against base_sha, plus untracked files."""
    stats: dict[str, FileChange] = {}
    proc = run_git(["-c", "core.quotepath=false", "diff", "--numstat", base_sha], cwd=worktree)
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or "git diff failed")
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        path = parts[-1].replace("\\", "/")
        if " => " in path:
            path = path.split(" => ")[-1].strip().strip("}")
        stats[path] = FileChange(
            path=path,
            additions=int(parts[0]) if parts[0].isdigit() else 0,
            deletions=int(parts[1]) if parts[1].isdigit() else 0,
        )
    others = run_git(["-c", "core.quotepath=false", "ls-files", "--others", "--exclude-standard"], cwd=worktree)
    if others.returncode != 0:
        raise GitError(others.stderr.strip() or "git ls-files failed")
    for raw in others.stdout.splitlines():
        path = raw.strip().replace("\\", "/")
        if not path or path in stats:
            continue
        stats[path] = FileChange(path=path, additions=_line_count(worktree / path), deletions=0)
    return [stats[key] for key in sorted(stats)]


def _line_count(path: Path) -> int:
    try:
        data = path.read_bytes()
    except OSError:
        return 0
    if b"\0" in data[:8000]:
        return 0
    text = data.decode("utf-8", errors="replace")
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def collect_changes(worktree: Path, base_ref: str) -> list[str]:
    """Tracked, staged and untracked paths that differ from base_ref."""
    names: set[str] = set()
    # Triple-dot is the task's commits. The other queries are uncommitted edits, not later commits on the base.
    queries = (
        ["diff", "--name-only", f"{base_ref}...HEAD"],
        ["diff", "--name-only", "HEAD"],
        ["diff", "--cached", "--name-only", "HEAD"],
        ["ls-files", "--others", "--exclude-standard"],
    )
    for query in queries:
        proc = run_git(["-c", "core.quotepath=false", *query], cwd=worktree)
        if proc.returncode != 0:
            raise GitError(proc.stderr.strip() or "git diff failed")
        for line in proc.stdout.splitlines():
            if line.strip():
                names.add(line.strip().replace("\\", "/"))
    return sorted(names)


def revert_out_of_scope(worktree: Path, allowed: list[str], denied: list[str]) -> list[str]:
    """Restore tracked files and delete untracked files outside the task scope."""
    reverted: list[str] = []
    for path in collect_changes(worktree, "HEAD"):
        if in_scope(path, allowed, denied):
            continue
        _restore(worktree, path)
        reverted.append(path)
    return reverted


def _restore(worktree: Path, rel: str) -> None:
    tracked = run_git(["cat-file", "-e", f"HEAD:{rel}"], cwd=worktree)
    if tracked.returncode == 0:
        restored = run_git(["restore", "--source=HEAD", "--staged", "--worktree", "--", rel], cwd=worktree)
        if restored.returncode != 0:
            raise GitError(restored.stderr.strip() or f"could not restore {rel}")
        return
    run_git(["reset", "-q", "--", rel], cwd=worktree)
    target = worktree / rel
    if target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)
    run_git(["clean", "-f", "--", rel], cwd=worktree)


def _same_dir(left: str, right: Path) -> bool:
    try:
        return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(str(right.resolve()))
    except OSError:
        return False


def observe_cwd(pid: int) -> CwdAttempt:
    """One look at a process tree: identity, liveness, children, and each cwd or the error."""
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return CwdAttempt(pid=pid, processes=[CwdProcessView(pid=pid, error="NoSuchProcess")])
    except psutil.Error as exc:
        return CwdAttempt(pid=pid, processes=[CwdProcessView(pid=pid, error=type(exc).__name__)])
    alive = False
    started: float | None = None
    try:
        alive = bool(root.is_running())
    except psutil.Error:
        alive = False
    try:
        started = float(root.create_time())
    except psutil.Error:
        started = None
    try:
        children = root.children(recursive=True)
    except psutil.Error:
        children = []
    processes: list[CwdProcessView] = []
    for proc in (root, *children):
        try:
            processes.append(CwdProcessView(pid=int(proc.pid), cwd=str(Path(proc.cwd()).resolve())))
        except (psutil.Error, OSError) as exc:
            processes.append(CwdProcessView(pid=int(proc.pid), error=type(exc).__name__))
    return CwdAttempt(
        pid=pid,
        pid_started=started,
        alive=alive,
        child_pids=[int(child.pid) for child in children],
        processes=processes,
    )


def confirm_cwd(
    pid: int,
    worktree: Path,
    reported: str,
    *,
    ready_at: float | None,
) -> tuple[bool, list[CwdAttempt]]:
    """The worker's reported directory and the OS directory of this pid must both be the worktree.

    A lookup that cannot see the process is inconclusive and is retried with backoff until 15 seconds
    after the worker reported ready. A visible mismatch fails immediately. Unknown is not a pass.
    """
    deadline = (ready_at if ready_at is not None else time.monotonic()) + 15
    attempts: list[CwdAttempt] = []
    delay = 0.05
    while True:
        attempt = observe_cwd(pid)
        attempts.append(attempt)
        own = next((item for item in attempt.processes if item.pid == pid and item.cwd), None)
        if own is not None:
            os_ok = _same_dir(own.cwd, worktree)
            reported_ok = _same_dir(reported, worktree)
            agreed = _same_dir(reported, Path(own.cwd))
            if os_ok and reported_ok and agreed:
                return True, []
            return False, attempts
        if time.monotonic() >= deadline:
            return False, attempts
        time.sleep(delay)
        delay = min(delay * 2, 1.0)


def _toplevel(worktree: Path) -> Path | None:
    if not worktree.exists():
        return None
    proc = run_git(["rev-parse", "--show-toplevel"], cwd=worktree)
    if proc.returncode != 0:
        return None
    return Path(proc.stdout.strip())


def verify_task_isolation(
    repo: Path,
    *,
    task_id: str,
    worktree: Path,
    run_id: str,
    base_ref: str,
    allowed_paths: list[str],
    denied_paths: list[str] | None = None,
    pid: int | None = None,
    reported_cwd: str | None = None,
    ready_at: float | None = None,
    cwd_required: bool = False,
    observe_pid: int | None = None,
) -> IsolationProof:
    denied = denied_paths or []
    top = _toplevel(worktree)
    registered = any(
        _same(item.path, worktree) and item.branch == branch_name(run_id, task_id) for item in list_worktrees(repo)
    )
    try:
        changes = collect_changes(worktree, base_ref) if worktree.exists() else []
    except GitError:
        changes = []
    offending = [path for path in changes if not in_scope(path, allowed_paths, denied)]
    cwd_ok = True
    attempts: list[CwdAttempt] = []
    if pid is None and cwd_required:
        cwd_ok = False
        if observe_pid is not None:
            seen = observe_cwd(observe_pid)
            attempts = [seen.model_copy(update={"pid": None, "error": "unresolved"})]
        else:
            attempts = [CwdAttempt(error="unresolved")]
    elif pid is not None:
        if not reported_cwd:
            cwd_ok = False
            seen = observe_cwd(pid)
            attempts = [seen.model_copy(update={"error": "missing worker.ready"})]
        else:
            cwd_ok, attempts = confirm_cwd(pid, worktree, reported_cwd, ready_at=ready_at)
    return IsolationProof(
        task_id=task_id,
        worktree_path=str(worktree),
        branch=branch_name(run_id, task_id),
        pid=pid,
        process_cwd_matches=cwd_ok,
        toplevel_matches=top is not None and _same(top, worktree),
        registered_in_worktree_list=registered,
        canary_absent=not (worktree / CANARY_NAME).exists(),
        changes_within_allowed_paths=not offending,
        offending_paths=offending,
        cwd_attempts=attempts,
    )


def snapshot_main(repo: Path) -> MainSnapshot:
    status = git_out(["status", "--porcelain=v1", "-uall"], repo)
    head = git_out(["rev-parse", "HEAD"], repo).strip()
    lines = tuple(sorted(line for line in status.splitlines() if line.strip()))
    return MainSnapshot(status=lines, head=head)


def verify_main(repo: Path, before: MainSnapshot) -> MainCheckoutProof:
    after = snapshot_main(repo)
    return MainCheckoutProof(
        status_before=list(before.status),
        status_after=list(after.status),
        head_before=before.head,
        head_after=after.head,
        canary_present=(repo / CANARY_NAME).is_file(),
    )


def _sibling_entries(repo: Path) -> list[Worktree]:
    main = Path(git_out(["rev-parse", "--show-toplevel"], repo).strip())
    return [item for item in list_worktrees(repo) if not _same(item.path, main)]


def snapshot_siblings(repo: Path) -> tuple[SiblingSnapshot, ...]:
    snaps: list[SiblingSnapshot] = []
    for item in _sibling_entries(repo):
        status = git_out(["status", "--porcelain=v1", "-uall"], item.path)
        lines = tuple(sorted(line for line in status.splitlines() if line.strip()))
        snaps.append(SiblingSnapshot(path=str(item.path.resolve()), head=item.head, status=lines))
    return tuple(snaps)


def verify_siblings(
    before: tuple[SiblingSnapshot, ...],
    after: tuple[SiblingSnapshot, ...],
    owned: set[Path],
) -> SiblingProof:
    """A change in a worktree that no task owns fails the proof."""
    previous = {snap.path: snap for snap in before}
    owned_paths = {os.path.normcase(str(path.resolve())) for path in owned}
    offending: list[str] = []
    for snap in after:
        if os.path.normcase(snap.path) in owned_paths:
            continue
        earlier = previous.get(snap.path)
        if earlier is None or earlier.head != snap.head or earlier.status != snap.status:
            offending.append(snap.path)
    return SiblingProof(ok=not offending, offending=tuple(offending))
