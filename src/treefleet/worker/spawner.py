"""Start one worker process. The orchestrator owns the process tree."""

from __future__ import annotations

import os
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import psutil

from treefleet.events import EventSink
from treefleet.git.cursor_scope import check_cursor_scope
from treefleet.git.worktrees import kill_tree

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


def interpreter_pid(pid: int, worktree: Path) -> int:
    """The venv launcher's cwd is a temp directory. The child interpreter holds the worktree."""
    target = os.path.normcase(str(worktree.resolve()))
    try:
        root = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return pid
    for proc in (root, *root.children(recursive=True)):
        try:
            cwd = os.path.normcase(str(Path(proc.cwd()).resolve()))
            name = proc.name().lower()
        except (psutil.Error, OSError):
            continue
        if cwd == target and "python" in name:
            return int(proc.pid)
    return pid


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
    proc = subprocess.Popen(
        [
            str(python),
            "-m",
            "treefleet.worker.main",
            "--port",
            str(port),
            "--worktree",
            str(worktree),
            "--token",
            token,
            "--run-id",
            run_id,
            "--task-id",
            task_id,
        ],
        cwd=worktree,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        shell=False,
    )
    handle.close()
    url = f"http://127.0.0.1:{port}"
    record = WorkerProcess(task_id=task_id, popen=proc, pid=proc.pid, url=url, worktree=worktree)
    if owner is not None:
        owner.append(record)
    try:
        _wait_for_card(url, token, proc)
    except Exception:
        kill_tree(proc.pid)
        raise
    record.pid = interpreter_pid(proc.pid, worktree)
    return record


def _wait_for_card(url: str, token: str, proc: subprocess.Popen[str], timeout_s: float = 40) -> None:
    deadline = time.monotonic() + timeout_s
    headers = {"Authorization": f"Bearer {token}"}
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("worker exited before the agent card was ready")
        try:
            response = httpx.get(url + "/.well-known/agent-card.json", headers=headers, timeout=2)
        except httpx.HTTPError:
            time.sleep(0.2)
            continue
        if response.status_code == 200:
            return
        time.sleep(0.2)
    raise TimeoutError(f"worker agent card was not ready within {timeout_s}s")


def worker_pids() -> list[int]:
    found: list[int] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        command = proc.info.get("cmdline") or []
        if any("treefleet.worker" in part for part in command):
            found.append(int(proc.info["pid"]))
    return found
