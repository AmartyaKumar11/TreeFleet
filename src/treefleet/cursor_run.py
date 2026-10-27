"""Headless cursor-agent. No CrewAI import. The model never sees this module's git calls."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from treefleet.contracts import CursorRunResult, CursorUsage, OutputLine, RunEvent
from treefleet.events import EventSink
from treefleet.git.proofs import collect_changes, revert_out_of_scope
from treefleet.git.worktrees import GitError, kill_tree
from treefleet.timing import record_step

_SECRET = re.compile(r"(sk-[A-Za-z0-9]+|apikey_[A-Za-z0-9]+|api[_-]?key\s*[:=]\s*\S+)", re.IGNORECASE)


def redact_line(text: str) -> str:
    cleaned = _SECRET.sub("[redacted]", text).replace("\r", " ").replace("\n", " ").strip()
    return cleaned[:200]


def redact_blob(text: str, limit: int = 2000) -> str:
    return _SECRET.sub("[redacted]", text)[:limit]


def command_for(cli: Path, worktree: Path, model: str | None, instruction: str) -> list[str]:
    prefix = _executable(cli)
    args = [*prefix, "--print", "--output-format", "json", "--workspace", str(worktree), "--trust"]
    if sys.platform == "win32":
        args.extend(["--sandbox", "disabled"])
    if model:
        args.extend(["--model", model])
    args.append(instruction)
    return args


def _executable(cli: Path) -> list[str]:
    if cli.suffix.lower() == ".py":
        prefix = [sys.executable, str(cli)]
        if cli.name == "s9_fakes.py":
            prefix.append("--shim")
        return prefix
    node = _cursor_node(cli)
    return node if node else [str(cli)]


def _cursor_node(cli: Path) -> list[str] | None:
    """Windows ships cursor-agent as a cmd that starts node. CreateProcess cannot run a cmd with shell=False."""
    if cli.suffix.lower() not in {".cmd", ".ps1"}:
        return None
    versions = sorted(cli.parent.glob("versions/*/node.exe"))
    if not versions:
        return None
    node = versions[-1]
    script = node.parent / "index.js"
    if not script.is_file():
        return None
    return [str(node), str(script)]


def _minimal_env() -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PATHEXT": os.environ.get("PATHEXT", ""),
        "TEMP": os.environ.get("TEMP", ""),
        "TMP": os.environ.get("TMP", ""),
    }
    cursor_key = os.environ.get("CURSOR_API_KEY")
    if cursor_key:
        env["CURSOR_API_KEY"] = cursor_key
    for name in ("TREEFLEET_FAKE_EDIT", "TREEFLEET_FAKE_OUTSIDE", "TREEFLEET_FAKE_SLEEP", "TREEFLEET_FAKE_NOTE"):
        if name in os.environ:
            env[name] = os.environ[name]
    return env


def _usage(stdout: str) -> CursorUsage | None:
    payload: object = None
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            break
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    return CursorUsage.model_validate(usage)


def _emit(sink: EventSink | None, run_id: str | None, stdout: str) -> None:
    if sink is None or not run_id:
        return
    for line in stdout.splitlines():
        redacted = redact_line(line)
        if not redacted:
            continue
        sink.emit_sync(
            RunEvent(
                run_id=run_id,
                seq=0,
                source="worker",
                type="output_line",
                payload=OutputLine(line=redacted).model_dump(),
            )
        )


def run_cursor(
    *,
    cli: Path,
    worktree: Path,
    instruction: str,
    allowed: list[str],
    denied: list[str],
    model: str | None,
    timeout_s: float,
    sink: EventSink | None = None,
    run_id: str | None = None,
) -> CursorRunResult:
    header = (
        f"Allowed paths: {', '.join(allowed)}. Denied paths: {', '.join(denied) or 'none'}. "
        "Edit only the allowed files. Do not explore or search the repository. "
        "Do not run git commands. Do not touch files outside the listed paths.\n"
    )
    started = time.monotonic()
    proc = subprocess.Popen(
        command_for(cli, worktree, model, header + instruction[:400]),
        cwd=worktree,
        env=_minimal_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    timed_out = False
    stdout = ""
    try:
        stdout, _ = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_tree(proc.pid)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        stdout = ""
    _emit(sink, run_id, stdout)
    record_step(
        "cursor",
        time.monotonic() - started,
        timed_out=timed_out,
        name=model or "",
        output_tail=redact_blob(stdout[-2000:]),
        sink=sink,
        run_id=run_id,
    )
    try:
        changed = collect_changes(worktree, "HEAD") if worktree.exists() else []
        reverted = revert_out_of_scope(worktree, allowed, denied) if worktree.exists() else []
    except GitError:
        changed, reverted = [], []
    tail = stdout[-2000:]
    return CursorRunResult(
        exit_code=-1 if timed_out else int(proc.returncode or 0),
        timed_out=timed_out,
        duration_s=round(time.monotonic() - started, 3),
        files_changed=changed,
        reverted_paths=reverted,
        output_tail=tail,
        usage=None if timed_out else _usage(stdout),
    )
