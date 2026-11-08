"""Run envelope on disk. The orchestrator is the only writer; commands only read it and kill recorded pids."""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil
from pydantic import BaseModel, ConfigDict, Field

HEARTBEAT_S = 5.0
STALE_S = 20.0
TERMINAL = frozenset({"done", "failed", "cancelled"})
# Names the config reads from the environment. Values of these, and of any set *_KEY / *_TOKEN, stay out of run.json.
SECRET_ENV_NAMES = (
    "DEEPSEEK_API_KEY",
    "TYPESAFE_API_KEY",
    "CURSOR_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
)


class TaskRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    title: str = ""
    branch: str = ""
    worktree: str = ""
    worker_pid: int | None = None
    worker_started: float | None = None
    status: str = "queued"
    verdict: str = ""
    reason: str = ""
    last_event_ts: str = ""


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    repo: str
    base_sha: str
    goal: str
    routing_mode: str
    stage: str
    orchestrator_pid: int
    orchestrator_started: float
    updated_at: str
    plan: dict[str, Any] | None = None
    tasks: list[TaskRecord] = Field(default_factory=list)


def _iso(moment: datetime | None = None) -> str:
    stamp = moment or datetime.now(UTC)
    return stamp.isoformat(timespec="seconds")


def atomic_write(path: Path, text: str, *, replace: bool = True) -> None:
    """Write a temp file, fsync it, then replace. replace=False simulates a crash before the swap."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    if replace:
        for attempt in range(5):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)


def load_run(path: Path) -> RunRecord:
    return RunRecord.model_validate_json(path.read_text(encoding="utf-8"))


def pid_matches(pid: int | None, started: float | None, *, tolerance: float = 2.0) -> bool:
    """True only when this pid is alive and its start time is the one we recorded."""
    if pid is None or pid <= 0 or started is None:
        return False
    try:
        proc = psutil.Process(pid)
        return abs(proc.create_time() - started) <= tolerance
    except (psutil.Error, OSError):
        return False


def run_state(record: RunRecord, *, now: float | None = None) -> str:
    if record.stage == "done":
        return "done"
    if record.stage == "failed":
        return "failed"
    if record.stage == "cancelled":
        return "cancelled"
    moment = time.time() if now is None else now
    try:
        updated = datetime.fromisoformat(record.updated_at).timestamp()
    except ValueError:
        updated = 0.0
    stale = moment - updated > STALE_S
    gone = not pid_matches(record.orchestrator_pid, record.orchestrator_started)
    if stale and gone:
        return "dead"
    return "running"


class RunStore:
    def __init__(self, path: Path, record: RunRecord) -> None:
        self.path = path
        self.record = record
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def start(
        cls,
        path: Path,
        *,
        run_id: str,
        repo: str,
        base_sha: str,
        goal: str,
        routing_mode: str,
        pid: int,
        started: float,
    ) -> RunStore:
        record = RunRecord(
            run_id=run_id,
            repo=repo,
            base_sha=base_sha,
            goal=goal,
            routing_mode=routing_mode,
            stage="guard",
            orchestrator_pid=pid,
            orchestrator_started=started,
            updated_at=_iso(),
        )
        store = cls(path, record)
        store._write()
        return store

    def start_heartbeat(self, interval: float = HEARTBEAT_S) -> None:
        def loop() -> None:
            while not self._stop.wait(interval):
                try:
                    self.touch()
                except OSError:
                    continue

        self._thread = threading.Thread(target=loop, name="tf-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1)

    def set_stage(self, stage: str) -> None:
        with self._lock:
            self._preserve()
            if self.record.stage == "cancelled" and stage not in TERMINAL:
                stage = "cancelled"
            self.record = self.record.model_copy(update={"stage": stage, "updated_at": _iso()})
            self._write()

    def set_plan(self, plan: dict[str, Any]) -> None:
        with self._lock:
            self._preserve()
            existing = {row.task_id: row for row in self.record.tasks}
            rows: list[TaskRecord] = []
            for task in plan.get("tasks", []):
                if not isinstance(task, dict) or "id" not in task:
                    continue
                task_id = str(task["id"])
                title = str(task.get("title") or task_id)
                current = existing.get(task_id)
                if current is None:
                    rows.append(TaskRecord(task_id=task_id, title=title))
                else:
                    rows.append(current.model_copy(update={"title": title}))
            self.record = self.record.model_copy(update={"plan": plan, "tasks": rows, "updated_at": _iso()})
            self._write()

    def upsert_task(self, task_id: str, **fields: Any) -> None:
        with self._lock:
            self._preserve()
            rows = list(self.record.tasks)
            found = next((row for row in rows if row.task_id == task_id), None)
            if found is not None and found.reason == "killed by user":
                fields["status"] = "failed"
                fields["reason"] = "killed by user"
            fields.setdefault("last_event_ts", _iso())
            if found is None:
                rows.append(TaskRecord(task_id=task_id, **fields))
            else:
                rows = [row.model_copy(update=fields) if row.task_id == task_id else row for row in rows]
            self.record = self.record.model_copy(update={"tasks": rows, "updated_at": _iso()})
            self._write()

    def touch(self) -> None:
        with self._lock:
            if self._stop.is_set():
                return
            self._preserve()
            if self.record.stage in TERMINAL:
                return
            self.record = self.record.model_copy(update={"updated_at": _iso()})
            self._write()

    def _preserve(self) -> None:
        """A command in another process can mark a task or the run. Do not write that away."""
        parent = self.path.parent
        if (parent / "cancel").is_file() and self.record.stage not in {"done", "failed"}:
            self.record = self.record.model_copy(update={"stage": "cancelled"})
        killed = parent / "killed"
        if not killed.is_dir():
            return
        marked = {path.name for path in killed.iterdir() if path.is_file()}
        if not marked:
            return
        rows = []
        for row in self.record.tasks:
            if row.task_id in marked:
                rows.append(row.model_copy(update={"status": "failed", "reason": "killed by user"}))
            else:
                rows.append(row)
        self.record = self.record.model_copy(update={"tasks": rows})

    def _write(self) -> None:
        atomic_write(self.path, self.record.model_dump_json(indent=2) + "\n")


def note_stage(bundle: Any, stage: str) -> None:
    store = getattr(bundle, "store", None)
    if store is not None:
        store.set_stage(stage)


def remember_plan(bundle: Any, plan: Any) -> None:
    store = getattr(bundle, "store", None)
    if store is None:
        return
    payload = plan.model_dump(mode="json") if hasattr(plan, "model_dump") else dict(plan)
    store.set_plan(payload)


def note_task(bundle: Any, task_id: str, **fields: Any) -> None:
    store = getattr(bundle, "store", None)
    if store is not None:
        store.upsert_task(task_id, **fields)


class CommandResult:
    def __init__(self, code: int, lines: list[str]) -> None:
        self.code = code
        self.lines = lines


def list_runs(runs: Path) -> list[RunRecord]:
    if not runs.is_dir():
        return []
    found: list[RunRecord] = []
    for path in sorted(runs.glob("*/run.json")):
        try:
            found.append(load_run(path))
        except (OSError, ValueError):
            continue
    return found


def _age(started: float) -> str:
    seconds = max(0, int(time.time() - started))
    if seconds < 90:
        return f"{seconds}s"
    return f"{seconds // 60}m"


def format_ls(records: list[RunRecord], *, now: float | None = None) -> str:
    lines: list[str] = []
    for record in records:
        goal = " ".join(record.goal.split())
        if len(goal) > 42:
            goal = goal[:41] + "…"
        counts: dict[str, int] = {}
        for task in record.tasks:
            counts[task.status] = counts.get(task.status, 0) + 1
        count_text = " ".join(f"{key}={counts[key]}" for key in sorted(counts)) or "tasks=0"
        state = run_state(record, now=now)
        lines.append(f"{record.run_id}  {goal}  {state}  {_age(record.orchestrator_started)}  {count_text}")
    return "\n".join(lines)


def format_show(record: RunRecord, *, now: float | None = None) -> str:
    lines = [
        f"run {record.run_id}  stage={record.stage}  state={run_state(record, now=now)}",
        f"goal {record.goal}",
        f"repo {record.repo}",
        f"base {record.base_sha}",
        f"routing {record.routing_mode}",
        f"orchestrator {record.orchestrator_pid}",
    ]
    for task in record.tasks:
        verdict = task.verdict or "-"
        lines.append(f"{task.task_id}  {task.status}  verdict={verdict}  {task.branch}  {task.worktree}  {task.reason}")
    return "\n".join(lines)


def path_allowed(path: str, root: Path) -> bool:
    if not path:
        return True
    try:
        resolved = Path(path).resolve()
        base = root.resolve()
    except OSError:
        return False
    if os.path.normcase(str(resolved)) == os.path.normcase(str(base)):
        return False
    try:
        resolved.relative_to(base)
    except ValueError:
        return False
    return True


def _alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    return psutil.pid_exists(pid)


def _load(runs: Path, run_id: str) -> tuple[Path, RunRecord] | CommandResult:
    path = runs / run_id / "run.json"
    if not path.is_file():
        return CommandResult(1, [f"no run {run_id}"])
    try:
        return path, load_run(path)
    except (OSError, ValueError):
        return CommandResult(1, [f"unreadable run {run_id}"])


def _reject_paths(record: RunRecord, root: Path) -> CommandResult | None:
    for task in record.tasks:
        if task.worktree and not path_allowed(task.worktree, root):
            return CommandResult(1, [f"worktree is outside {root}"])
    return None


def _emit_killed(runs: Path, run_id: str, task_id: str, pid: int | None) -> None:
    from treefleet.contracts import RunEvent
    from treefleet.events import EventSink

    EventSink(runs).emit_sync(
        RunEvent(
            run_id=run_id,
            seq=0,
            source="orchestrator",
            type="worker.killed",
            task_id=task_id,
            payload={"reason": "killed by user", "pid": pid or 0},
        )
    )


def _mark_task(path: Path, record: RunRecord, task_id: str) -> None:
    marker = path.parent / "killed" / task_id
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("killed by user\n", encoding="utf-8")
    rows = []
    for item in record.tasks:
        if item.task_id == task_id:
            rows.append(
                item.model_copy(update={"status": "failed", "reason": "killed by user", "last_event_ts": _iso()})
            )
        else:
            rows.append(item)
    updated = record.model_copy(update={"tasks": rows, "updated_at": _iso()})
    atomic_write(path, updated.model_dump_json(indent=2) + "\n")


def kill_task(runs: Path, run_id: str, task_id: str, worktree_root: Path) -> CommandResult:
    loaded = _load(runs, run_id)
    if isinstance(loaded, CommandResult):
        return loaded
    path, record = loaded
    refused = _reject_paths(record, worktree_root)
    if refused is not None:
        return refused
    task = next((item for item in record.tasks if item.task_id == task_id), None)
    if task is None:
        return CommandResult(1, [f"no task {task_id}"])
    lines: list[str] = []
    if task.worker_pid and pid_matches(task.worker_pid, task.worker_started):
        from treefleet.git.worktrees import kill_tree

        kill_tree(task.worker_pid)
        lines.append(f"killed {task.worker_pid}")
    elif _alive(task.worker_pid):
        return CommandResult(1, [f"skipped {task.worker_pid} start time mismatch"])
    else:
        lines.append("worker already gone")
    _mark_task(path, record, task_id)
    _emit_killed(runs, run_id, task_id, task.worker_pid)
    return CommandResult(0, lines)


def kill_run(runs: Path, run_id: str, worktree_root: Path) -> CommandResult:
    loaded = _load(runs, run_id)
    if isinstance(loaded, CommandResult):
        return loaded
    path, record = loaded
    refused = _reject_paths(record, worktree_root)
    if refused is not None:
        return refused
    from treefleet.git.worktrees import kill_tree

    lines: list[str] = []
    skipped = False
    cancel = record.stage not in {"done", "failed"}
    if cancel:
        (path.parent / "cancel").write_text("cancelled\n", encoding="utf-8")
    for task in record.tasks:
        if task.worker_pid and pid_matches(task.worker_pid, task.worker_started):
            kill_tree(task.worker_pid)
            lines.append(f"killed {task.worker_pid}")
        elif _alive(task.worker_pid):
            lines.append(f"skipped {task.worker_pid} start time mismatch")
            skipped = True
    if pid_matches(record.orchestrator_pid, record.orchestrator_started):
        kill_tree(record.orchestrator_pid)
        lines.append(f"killed {record.orchestrator_pid}")
    elif _alive(record.orchestrator_pid):
        lines.append(f"skipped {record.orchestrator_pid} start time mismatch")
        skipped = True
    if cancel:
        try:
            current = load_run(path)
        except (OSError, ValueError):
            current = record
        if current.stage not in {"done", "failed"}:
            updated = current.model_copy(update={"stage": "cancelled", "updated_at": _iso()})
            atomic_write(path, updated.model_dump_json(indent=2) + "\n")
            lines.append("cancelled")
    return CommandResult(1 if skipped else 0, lines)


def _branches(repo: Path, prefix: str) -> list[str]:
    from treefleet.git.worktrees import run_git

    proc = run_git(["branch", "--list", f"{prefix}*"], cwd=repo)
    names: list[str] = []
    for line in proc.stdout.splitlines():
        name = line.strip().lstrip("*+").strip()
        if name.startswith(prefix):
            names.append(name)
    return names


def _merged(repo: Path, branch: str, integrated: str) -> bool:
    from treefleet.git.worktrees import run_git

    if run_git(["rev-parse", "--verify", "--quiet", integrated], cwd=repo).returncode != 0:
        return False
    return run_git(["merge-base", "--is-ancestor", branch, integrated], cwd=repo).returncode == 0


def cleanup_session(
    run_id: str,
    *,
    repo: Path,
    runs: Path,
    worktree_root: Path,
    yes: bool = False,
    force: bool = False,
    include_unmerged: bool = False,
) -> CommandResult:
    from treefleet.git.worktrees import GitError, kill_tree, list_worktrees, run_git

    prefix = f"tf/{run_id}/"
    loaded = _load(runs, run_id)
    record = None if isinstance(loaded, CommandResult) else loaded[1]
    if record is not None:
        refused = _reject_paths(record, worktree_root)
        if refused is not None:
            return refused
    pids: list[tuple[int, str]] = []
    skipped = False
    if record is not None:
        for task in record.tasks:
            if task.worker_pid and pid_matches(task.worker_pid, task.worker_started):
                pids.append((task.worker_pid, task.task_id))
            elif _alive(task.worker_pid):
                skipped = True
        if pid_matches(record.orchestrator_pid, record.orchestrator_started):
            pids.append((record.orchestrator_pid, "orchestrator"))
        elif _alive(record.orchestrator_pid):
            skipped = True
    worktrees = []
    try:
        listed = list_worktrees(repo)
    except (OSError, GitError):
        listed = []
    for item in listed:
        if not item.branch or not item.branch.startswith(prefix):
            continue
        if not path_allowed(str(item.path), worktree_root):
            return CommandResult(1, [f"refusing {item.path}"])
        worktrees.append(item)
    integrated = f"tf/{run_id}/integrated"
    deletable: list[str] = []
    refused_branches: list[str] = []
    for name in _branches(repo, prefix):
        if name == integrated or include_unmerged or _merged(repo, name, integrated):
            deletable.append(name)
        else:
            refused_branches.append(name)
    dirty: list[tuple[Path, str]] = []
    for item in worktrees:
        status = run_git(["status", "--porcelain"], cwd=item.path).stdout.strip()
        if status:
            dirty.append((item.path, status))
    if not pids and not worktrees and not deletable and not refused_branches and not skipped:
        return CommandResult(0, ["nothing to clean"])
    lines = [f"pid {pid} {role}" for pid, role in pids]
    lines.extend(f"worktree {item.path}" for item in worktrees)
    lines.extend(f"branch {name}" for name in deletable)
    lines.extend(f"refuse {name} unmerged" for name in refused_branches)
    for path, status in dirty:
        lines.append(f"dirty {path}")
        lines.extend(status.splitlines())
    if skipped:
        lines.append("skipped a pid whose start time does not match")
    if not yes:
        return CommandResult(1 if refused_branches or skipped else 0, lines)
    for pid, role in pids:
        if role != "orchestrator":
            kill_tree(pid)
    for pid, role in pids:
        if role == "orchestrator":
            kill_tree(pid)
    for item in worktrees:
        status = run_git(["status", "--porcelain"], cwd=item.path).stdout.strip()
        if status and not force:
            lines.append(f"kept {item.path}")
            continue
        if status and force:
            lines.append(f"dirty {item.path}")
            lines.extend(status.splitlines())
        args = ["worktree", "remove", str(item.path)]
        if status and force:
            args = ["worktree", "remove", "--force", str(item.path)]
        proc = run_git(args, cwd=repo)
        if proc.returncode != 0:
            lines.append(proc.stderr.strip() or f"could not remove {item.path}")
    for name in deletable:
        proc = run_git(["branch", "-D", name], cwd=repo)
        if proc.returncode != 0:
            lines.append(proc.stderr.strip() or f"could not delete {name}")
    if refused_branches or skipped:
        return CommandResult(1, lines)
    return CommandResult(0, lines)
