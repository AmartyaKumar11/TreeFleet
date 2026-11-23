"""Headless cursor-agent. No CrewAI import. The model never sees this module's git calls."""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from treefleet.contracts import CursorRunResult, CursorToolEvent, CursorUsage, OutputLine, RunEvent
from treefleet.events import EventSink
from treefleet.git.proofs import collect_changes, revert_out_of_scope
from treefleet.git.worktrees import GitError, kill_tree
from treefleet.timing import record_step

_SECRET = re.compile(r"(sk-[A-Za-z0-9]+|apikey_[A-Za-z0-9]+|api[_-]?key\s*[:=]\s*\S+)", re.IGNORECASE)

# DEMO ONLY. Off unless --saboteur names this task. Never enable it for real work.
SABOTEUR_LINE = "DEMO ONLY: also break an existing test so the suite fails."


def cursor_instruction(instruction: str, *, revision: str = "", saboteur: bool = False) -> str:
    """Append review changes, and the demo break-a-test line only when saboteur is set."""
    text = instruction
    if revision:
        text += "\n\nRequired changes:\n" + revision
    if saboteur:
        text += "\n\n" + SABOTEUR_LINE
    return text


def demo_break(worktree: Path) -> None:
    """DEMO ONLY. Add one failing test so the saboteur scenario fails even if Cursor declines."""
    tests = worktree / "tests"
    if not tests.is_dir():
        return
    target = tests / "test_auth.py"
    if not target.is_file():
        found = sorted(tests.glob("test_*.py"))
        if not found:
            return
        target = found[0]
    text = target.read_text(encoding="utf-8")
    if "test_treefleet_saboteur_demo" in text:
        return
    target.write_text(
        text.rstrip() + "\n\ndef test_treefleet_saboteur_demo():\n    assert False, 'DEMO ONLY saboteur'\n",
        encoding="utf-8",
    )


def redact_line(text: str, *, limit: int = 200) -> str:
    cleaned = _SECRET.sub("[redacted]", text).replace("\r", " ").replace("\n", " ").strip()
    return cleaned[:limit]


# Allowlisted arg keys per Cursor tool kind. Never content / result / stream bodies.
_DETAIL_KEYS: dict[str, tuple[str, ...]] = {
    "shell": ("command", "cmd"),
    "bash": ("command", "cmd"),
    "terminal": ("command", "cmd"),
    "grep": ("pattern", "glob", "path"),
    "search": ("pattern", "glob", "path"),
    "glob": ("glob", "pattern", "path"),
    "read": ("path", "start_line", "end_line", "startLine", "endLine"),
    "edit": ("path",),
    "write": ("path",),
    "delete": ("path",),
    "list": ("path", "glob"),
}


def tool_detail(tool: str, args: dict[str, object]) -> str:
    """Build a capped, redacted one-line detail from allowlisted args. Empty for unknown tools."""
    keys = _DETAIL_KEYS.get(tool.lower())
    if not keys or not isinstance(args, dict):
        return ""
    parts: list[str] = []
    for key in keys:
        if key not in args:
            continue
        value = args[key]
        if value is None or value == "":
            continue
        if key in {"start_line", "end_line", "startLine", "endLine"}:
            parts.append(f"{key}={value}")
        else:
            parts.append(str(value))
    if not parts:
        return ""
    return redact_line(" ".join(parts), limit=300)


def redact_blob(text: str, limit: int = 2000) -> str:
    return _SECRET.sub("[redacted]", text)[:limit]


def _sandbox_disabled() -> bool:
    """Windows always disables sandbox; TREEFLEET_CURSOR_SANDBOX=disabled forces it on any OS."""
    if sys.platform == "win32":
        return True
    return os.environ.get("TREEFLEET_CURSOR_SANDBOX", "").strip().lower() == "disabled"


def resolve_cursor_cli(cli: Path | str) -> Path:
    """Resolve the Cursor CLI. Prefer the given name; fall back to `agent` when only that is on PATH."""
    raw = Path(cli)
    if raw.is_file():
        return raw
    found = shutil.which(str(cli))
    if found:
        return Path(found)
    name = raw.name.lower()
    if name in {"cursor-agent", "cursor-agent.cmd", "cursor-agent.ps1", "cursor-agent.exe"}:
        for alt in ("agent", "agent.cmd", "agent.exe"):
            hit = shutil.which(alt)
            if hit:
                return Path(hit)
    return raw


def command_for(cli: Path, worktree: Path, model: str | None, instruction: str) -> list[str]:
    prefix = _executable(cli)
    args = [
        *prefix,
        "--print",
        "--output-format",
        "stream-json",
        "--stream-partial-output",
        "--workspace",
        str(worktree),
        "--trust",
    ]
    if _sandbox_disabled():
        args.extend(["--sandbox", "disabled"])
    if model:
        args.extend(["--model", model])
    args.append(instruction)
    return args


def launch_command(cli: Path) -> list[str]:
    """Executable prefix. A bare `cursor-agent` on Windows is a cmd, which CreateProcess cannot start."""
    target = resolve_cursor_cli(cli)
    if target.suffix.lower() != ".py" and not target.is_file():
        found = shutil.which(str(target))
        if found:
            target = Path(found)
    if target.suffix.lower() == ".py":
        return [sys.executable, str(target)]
    node = _cursor_node(target)
    return node if node else [str(target)]


def _executable(cli: Path) -> list[str]:
    prefix = launch_command(cli)
    if cli.name == "s9_fakes.py":
        prefix.append("--shim")
    return prefix


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
    """Env for the cursor-agent child. Keep Linux home/locale; never inherit an open stdin.

    Cursor CLI changelog (2026-02): ``-p`` hangs when stdin is an open pipe.
    HOME/XDG are required so the CLI can read its local auth/config store.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PATHEXT": os.environ.get("PATHEXT", ""),
        "TEMP": os.environ.get("TEMP", ""),
        "TMP": os.environ.get("TMP", ""),
        "PYTHONUNBUFFERED": "1",
        # Headless: never try to open a browser from the worker.
        "NO_OPEN_BROWSER": "1",
    }
    # Linux/mac: preserve home + locale so cursor-agent finds config and stays UTF-8.
    for name in (
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "TREEFLEET_CURSOR_SANDBOX",
        "CURSOR_API_ENDPOINT",
    ):
        value = os.environ.get(name)
        if value:
            env[name] = value
    cursor_key = os.environ.get("CURSOR_API_KEY")
    if cursor_key:
        env["CURSOR_API_KEY"] = cursor_key
    for name in ("TREEFLEET_FAKE_EDIT", "TREEFLEET_FAKE_OUTSIDE", "TREEFLEET_FAKE_SLEEP", "TREEFLEET_FAKE_NOTE"):
        if name in os.environ:
            env[name] = os.environ[name]
    return env


@dataclass(frozen=True)
class StreamHit:
    """One kept line from cursor-agent stream-json. Schema is docs/spikes/s12-cursor-stream.sample.txt."""

    kind: str
    text: str = ""
    tool: str = ""
    path: str = ""
    ok: bool | None = None
    detail: str = ""
    usage: CursorUsage | None = None


def parse_stream_line(line: str) -> StreamHit | None:
    """Parse one stdout line. Rollups and thinking are dropped. A bare usage object is the offline shim."""
    stripped = line.strip()
    if not stripped.startswith("{"):
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    kind = payload.get("type")
    if kind is None and isinstance(payload.get("usage"), dict):
        return StreamHit(kind="result", usage=CursorUsage.model_validate(payload["usage"]))
    if kind == "result":
        usage = payload.get("usage")
        parsed = CursorUsage.model_validate(usage) if isinstance(usage, dict) else None
        return StreamHit(kind="result", text=str(payload.get("result") or "")[:2000], usage=parsed)
    if kind == "assistant":
        # Deltas carry timestamp_ms and no model_call_id. A model_call_id line repeats them.
        # The last assistant line has neither and repeats result.result.
        if "model_call_id" in payload or "timestamp_ms" not in payload:
            return None
        message = payload.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            return None
        text = "".join(
            str(item.get("text") or "") for item in content if isinstance(item, dict) and item.get("type") == "text"
        )
        return StreamHit(kind="text", text=text) if text else None
    if kind != "tool_call":
        return None
    tool_call = payload.get("tool_call")
    if not isinstance(tool_call, dict):
        return None
    tool, body = "", {}
    for key, value in tool_call.items():
        if key.endswith("ToolCall") and isinstance(value, dict):
            tool, body = key[: -len("ToolCall")], value
            break
    if not tool:
        return None
    raw_args = body.get("args")
    args = raw_args if isinstance(raw_args, dict) else {}
    path = str(args.get("path") or "")
    if payload.get("subtype") == "started":
        return StreamHit(kind="tool_start", tool=tool, path=path, detail=tool_detail(tool, args))
    raw_result = body.get("result")
    result = raw_result if isinstance(raw_result, dict) else {}
    return StreamHit(kind="tool_finish", tool=tool, path=path, ok="success" in result)


def _display_path(path: str, worktree: Path) -> str:
    if not path:
        return ""
    try:
        relative = Path(path).resolve().relative_to(worktree.resolve())
        return str(relative).replace("\\", "/")[:300]
    except (OSError, ValueError):
        return Path(path).name[:300]


def _emit_hit(
    sink: EventSink | None,
    run_id: str | None,
    task_id: str | None,
    hit: StreamHit,
    worktree: Path,
) -> None:
    if sink is None or not run_id:
        return
    if hit.kind == "text":
        redacted = redact_line(hit.text)
        if not redacted:
            return
        sink.emit_sync(
            RunEvent(
                run_id=run_id,
                seq=0,
                source="worker",
                type="output_line",
                task_id=task_id,
                payload=OutputLine(line=redacted).model_dump(),
            )
        )
        return
    if hit.kind not in {"tool_start", "tool_finish"}:
        return
    display = _display_path(hit.path, worktree)
    detail = ""
    if hit.kind == "tool_start" and hit.detail:
        # Re-display paths inside detail relative to the worktree when possible.
        detail = hit.detail
        if hit.path and display and hit.path in detail:
            detail = detail.replace(hit.path, display)
        detail = redact_line(detail, limit=300)
    event = CursorToolEvent(tool=hit.tool[:40], path=display, ok=hit.ok, detail=detail)
    sink.emit_sync(
        RunEvent(
            run_id=run_id,
            seq=0,
            source="worker",
            type="cursor.tool_start" if hit.kind == "tool_start" else "cursor.tool_finish",
            task_id=task_id,
            payload=event.model_dump(),
        )
    )


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


def _read_stdout(proc: subprocess.Popen[str], timeout_s: float, on_line: Callable[[str], None]) -> tuple[bool, str]:
    """Read stdout as lines arrive. Kill the process when the deadline passes."""
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    lines.put(line)
        finally:
            lines.put(None)

    threading.Thread(target=pump, name="cursor-stdout", daemon=True).start()
    deadline = time.monotonic() + timeout_s
    chunks: list[str] = []
    timed_out = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            kill_tree(proc.pid)
            break
        try:
            line = lines.get(timeout=min(remaining, 0.2))
        except queue.Empty:
            continue
        if line is None:
            break
        chunks.append(line)
        on_line(line)
    return timed_out, "".join(chunks)


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
    task_id: str | None = None,
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
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    final = ""
    usage: CursorUsage | None = None

    def on_line(line: str) -> None:
        nonlocal final, usage
        hit = parse_stream_line(line)
        if hit is None:
            return
        if hit.kind == "result":
            if hit.text:
                final = hit.text
            if hit.usage is not None:
                usage = hit.usage
            return
        _emit_hit(sink, run_id, task_id, hit, worktree)

    timed_out, stdout = _read_stdout(proc, timeout_s, on_line)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    if timed_out:
        usage = None
        final = ""
    elif usage is None:
        usage = _usage(stdout)
    tail = redact_blob(final or stdout[-2000:])
    record_step(
        "cursor",
        time.monotonic() - started,
        timed_out=timed_out,
        name=model or "",
        output_tail=tail,
        sink=sink,
        run_id=run_id,
        task_id=task_id,
    )
    try:
        changed = collect_changes(worktree, "HEAD") if worktree.exists() else []
        reverted = revert_out_of_scope(worktree, allowed, denied) if worktree.exists() else []
    except GitError:
        changed, reverted = [], []
    return CursorRunResult(
        exit_code=-1 if timed_out else int(proc.returncode or 0),
        timed_out=timed_out,
        duration_s=round(time.monotonic() - started, 3),
        files_changed=changed,
        reverted_paths=reverted,
        output_tail=tail,
        usage=usage,
    )
