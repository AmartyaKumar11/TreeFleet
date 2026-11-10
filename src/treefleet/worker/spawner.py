"""Start one worker process. The orchestrator owns the process tree."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import psutil

from treefleet.events import EventSink
from treefleet.git.cursor_scope import check_cursor_scope
from treefleet.git.worktrees import kill_tree
from treefleet.procgroup import _linux_pdeathsig, bind_worker, resume_process

_KEEP = (
    "SystemRoot",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "PATH",
    "PATHEXT",
    "SYSTEMDRIVE",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "HOMEDRIVE",
    "HOMEPATH",
    "COMSPEC",
)


@dataclass
class WorkerProcess:
    task_id: str
    popen: subprocess.Popen[str]
    pid: int
    url: str
    worktree: Path
    interpreter_pid: int | None = None
    interpreter_started: float | None = None
    reported_cwd: str | None = None
    ready_at: float | None = None
    ready_ts: str | None = None


def before_spawn(worktree: Path, run_id: str, sink: EventSink | None = None, task_id: str | None = None) -> list[str]:
    """Fail the task when the worktree has untracked Cursor config. Return warnings for tracked files."""
    return check_cursor_scope(worktree, run_id=run_id, sink=sink, task_id=task_id)


def worker_env(token: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """DeepSeek key, per-run token, Cursor credential, and the Windows variables Python needs."""
    env = {"PYTHONUNBUFFERED": "1", "AUTH_TOKEN": token}
    for key in _KEEP:
        value = os.environ.get(key)
        if value:
            env[key] = value
    for key in ("DEEPSEEK_API_KEY", "CURSOR_API_KEY"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    if extra:
        env.update(extra)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def free_port(start: int, taken: set[int]) -> int:
    for port in range(start, start + 50):
        if port in taken:
            continue
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"no free worker port from {start}")


def crew_python(worker_python: str) -> Path:
    """The CrewAI interpreter. The path recorded on a run is the check interpreter."""
    return Path(worker_python)


def worker_command(
    *,
    python: Path,
    worktree: Path,
    run_id: str,
    task_id: str,
    token: str,
    port: int,
    events: Path,
) -> list[str]:
    """Argv for one worker process. `python` is the CrewAI interpreter."""
    return [
        str(python),
        "-m",
        "treefleet.worker.main",
        "--port",
        str(port),
        "--worktree",
        str(worktree),
        # One argument. token_urlsafe can start with "-", which argparse reads as a flag.
        f"--token={token}",
        "--run-id",
        run_id,
        "--task-id",
        task_id,
        "--events",
        str(events),
    ]


def last_log_line(path: Path) -> str:
    """The last non-empty line of a worker log. Empty when the log was not written."""
    if not path.is_file():
        return ""
    lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    return lines[-1][:300] if lines else ""


def spawn_worker(
    *,
    python: Path,
    worktree: Path,
    run_id: str,
    task_id: str,
    token: str,
    port: int,
    env: dict[str, str],
    log_path: Path,
    owner: list[WorkerProcess] | None = None,
) -> WorkerProcess:
    """Start the worker venv interpreter and wait until its agent card answers."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8")
    command = worker_command(
        python=python,
        worktree=worktree,
        run_id=run_id,
        task_id=task_id,
        token=token,
        port=port,
        events=log_path.parent / "events.jsonl",
    )
    if sys.platform == "win32":
        # Suspend until the process is in the job, so the interpreter it spawns inherits the job.
        proc = subprocess.Popen(
            command,
            cwd=worktree,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            shell=False,
            creationflags=0x4,
        )
    elif sys.platform == "linux":
        proc = subprocess.Popen(
            command,
            cwd=worktree,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            shell=False,
            start_new_session=True,
            preexec_fn=_linux_pdeathsig,
        )
    else:
        proc = subprocess.Popen(
            command,
            cwd=worktree,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            shell=False,
            start_new_session=True,
        )
    try:
        bind_worker(proc.pid)
    except RuntimeError:
        kill_tree(proc.pid)
        handle.close()
        raise
    resume_process(proc.pid)
    handle.close()
    url = f"http://127.0.0.1:{port}"
    record = WorkerProcess(task_id=task_id, popen=proc, pid=proc.pid, url=url, worktree=worktree)
    if owner is not None:
        owner.append(record)
    events = log_path.parent / "events.jsonl"
    deadline = time.monotonic() + 120
    try:
        ready = _wait_for_ready(events, task_id, proc, deadline)
        if ready is not None:
            raw_payload = ready.get("payload")
            reported = raw_payload.get("cwd") if isinstance(raw_payload, dict) else ""
            record.reported_cwd = str(reported or "")
            record.ready_at = time.monotonic()
            record.ready_ts = str(ready.get("ts") or "")
            raw_pid = raw_payload.get("pid") if isinstance(raw_payload, dict) else None
            reported_pid = raw_pid if isinstance(raw_pid, int) else None
            resolved = resolve_interpreter(
                proc.pid,
                task_id,
                reported_pid,
                deadline=min(deadline, record.ready_at + 15),
            )
            if resolved is not None:
                record.interpreter_pid, record.interpreter_started = resolved
                try:
                    bind_worker(record.interpreter_pid)
                except RuntimeError:
                    kill_tree(proc.pid)
                    raise
        _wait_for_card(url, token, proc, deadline, record.interpreter_pid)
    except Exception:
        kill_tree(proc.pid)
        raise
    return record


def resolve_interpreter(
    root_pid: int,
    task_id: str,
    reported_pid: int | None,
    *,
    deadline: float,
) -> tuple[int, float] | None:
    """The python running treefleet.worker.main for this task. Never the venv launcher.

    A process list that cannot be read is retried until deadline (15s after worker.ready).
    No match by then is unresolved.
    """
    while True:
        found = _match_interpreter(root_pid, task_id, reported_pid)
        if found is not None:
            return found
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)


def _command_match(pid: int, task_id: str) -> tuple[int, float] | None:
    try:
        proc = psutil.Process(pid)
        command = [str(part) for part in proc.cmdline()]
        name = proc.name().lower()
        started = float(proc.create_time())
    except (psutil.Error, OSError):
        return None
    if "python" not in name or not _is_worker_command(command, task_id):
        return None
    return int(proc.pid), started


def _match_interpreter(
    root_pid: int,
    task_id: str,
    reported_pid: int | None,
) -> tuple[int, float] | None:
    if reported_pid is not None:
        direct = _command_match(reported_pid, task_id)
        if direct is not None:
            return direct
    try:
        root = psutil.Process(root_pid)
    except psutil.NoSuchProcess:
        return _scan_worker(task_id, root_pid)
    try:
        procs = [root, *root.children(recursive=True)]
    except psutil.Error:
        procs = [root]
    matches: list[tuple[int, float]] = []
    for proc in procs:
        found = _command_match(int(proc.pid), task_id)
        if found is not None:
            matches.append(found)
    children = [item for item in matches if item[0] != root_pid]
    # The Windows venv python is a launcher with the same command line and a temp cwd.
    # The interpreter is its child. A lone match is the interpreter itself.
    if children:
        return children[-1]
    return matches[0] if matches else None


def _scan_worker(task_id: str, root_pid: int) -> tuple[int, float] | None:
    """The interpreter after the venv launcher has exited and the child was reparented."""
    launcher: tuple[int, float] | None = None
    for proc in psutil.process_iter():
        found = _command_match(int(proc.pid), task_id)
        if found is None:
            continue
        if found[0] == root_pid:
            launcher = found
            continue
        return found
    return launcher


def _is_worker_command(command: list[str], task_id: str) -> bool:
    if not any(part == "treefleet.worker.main" or part.endswith("treefleet.worker.main") for part in command):
        return False
    for index, part in enumerate(command):
        if part == "--task-id" and index + 1 < len(command) and command[index + 1] == task_id:
            return True
    return False


def _wait_for_ready(
    events: Path,
    task_id: str,
    _proc: subprocess.Popen[str],
    deadline: float,
) -> dict[str, object] | None:
    from treefleet.events import read_events

    while time.monotonic() < deadline:
        if events.is_file():
            rows, _skipped = read_events(events)
            for item in rows:
                if item.get("type") == "worker.ready" and item.get("task_id") == task_id:
                    return item
        # The venv launcher can exit before the child writes worker.ready. That is not the worker exiting.
        time.sleep(0.05)
    return None


def _wait_for_card(
    url: str,
    token: str,
    _proc: subprocess.Popen[str],
    deadline: float,
    interpreter_pid: int | None,
) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    missing_since: float | None = None
    while time.monotonic() < deadline:
        if interpreter_pid is not None and not psutil.pid_exists(interpreter_pid):
            if missing_since is None:
                missing_since = time.monotonic()
            elif time.monotonic() - missing_since > 2:
                raise RuntimeError("worker exited before the agent card was ready")
        else:
            missing_since = None
        try:
            response = httpx.get(url + "/.well-known/agent-card.json", headers=headers, timeout=2)
        except httpx.HTTPError:
            time.sleep(0.2)
            continue
        if response.status_code == 200:
            return
        time.sleep(0.2)
    raise TimeoutError("worker agent card was not ready within 120s")


def worker_pids() -> list[int]:
    found: list[int] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        command = proc.info.get("cmdline") or []
        if any("treefleet.worker" in part for part in command):
            found.append(int(proc.info["pid"]))
    return found
