"""Read-only filesystem tools for one worktree. No write tool exists."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x, the version CrewAI pins
    from mcp.server.fastmcp import FastMCP as MCPServer  # pyright: ignore[reportAttributeAccessIssue]

from treefleet.contracts import CheckResult, Limits
from treefleet.git.worktrees import kill_tree, run_git

_CODES = (
    "PATH_ESCAPE",
    "PATH_DENIED",
    "NOT_FOUND",
    "TOO_LARGE",
    "NOT_ALLOWED",
    "TIMEOUT",
    "BAD_ARGS",
    "INTERNAL",
)
_DEVICES = {"CON", "PRN", "AUX", "NUL", "COM1", "LPT1"}
_LOOKALIKE = str.maketrans({"\uff0e": ".", "\u2024": ".", "\uff0f": "/", "\u2215": "/", "\u2044": "/"})
_HIDDEN = "\u200b\u200c\u200d\ufeff\u202a\u202b\u202c\u202d\u202e"


class PathGuardError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _err(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "data": None, "error": {"code": code, "message": message}}


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "data": data, "error": None}


def resolve_inside(root: Path, raw: str) -> Path:
    if not raw or any(char in raw for char in _HIDDEN) or len(raw) > 240:
        raise PathGuardError("PATH_ESCAPE", "path is empty, hidden, or too long")
    folded = raw.translate(_LOOKALIKE).replace("\\", "/")
    if folded.startswith("/") or (len(folded) > 1 and folded[1] == ":"):
        raise PathGuardError("PATH_ESCAPE", raw)
    parts = [part for part in folded.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise PathGuardError("PATH_ESCAPE", raw)
    if any(part.lower().split(".")[0] in _DEVICES for part in parts):
        raise PathGuardError("PATH_ESCAPE", raw)
    if ".git" in parts:
        raise PathGuardError("PATH_DENIED", raw)
    root_real = Path(os.path.realpath(root))
    current = root_real
    for part in parts:
        current = current / part
        if current.is_symlink():
            target = Path(os.path.realpath(current))
            if not _inside(target, root_real):
                raise PathGuardError("PATH_ESCAPE", raw)
    resolved = Path(os.path.realpath(root_real.joinpath(*parts))) if parts else root_real
    if not _inside(resolved, root_real):
        raise PathGuardError("PATH_ESCAPE", raw)
    return resolved


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _tail(text: str, limit: int = 4000) -> str:
    if len(text) <= 6000:
        return text[-limit:]
    head = text[:2000]
    tail = text[-2000:]
    return f"{head}\n... truncated ...\n{tail}"[-limit:]


def execute_check(root: Path, checks: dict[str, str], name: str) -> dict[str, Any]:
    command = checks.get(name)
    if command is None:
        return _err("NOT_ALLOWED", name)
    try:
        argv = shlex.split(command, posix=os.name != "nt")
    except ValueError as exc:
        return _err("BAD_ARGS", str(exc))
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PATHEXT": os.environ.get("PATHEXT", ""),
    }
    try:
        proc = subprocess.run(
            argv,
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _err("TIMEOUT", name)
    result = CheckResult(
        name=name,
        command=command,
        exit_code=proc.returncode,
        passed=proc.returncode == 0,
        stdout_tail=_tail(proc.stdout),
        stderr_tail=_tail(proc.stderr),
    )
    return _ok(result.model_dump())


def build_fs_server(root: Path, checks: dict[str, str]) -> MCPServer:
    server: MCPServer[Any] = MCPServer("worktree-fs-mcp")

    @server.tool()
    def list_dir(path: str = ".", depth: int = 1) -> dict[str, Any]:
        """List files under a relative path. Hides .git."""
        if depth < 1 or depth > 3:
            return _err("BAD_ARGS", "depth must be 1 to 3")
        try:
            base = resolve_inside(root, path)
        except PathGuardError as exc:
            return _err(exc.code, str(exc))
        if not base.is_dir():
            return _err("NOT_FOUND", path)
        entries: list[dict[str, Any]] = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [name for name in dirnames if name != ".git"]
            here = Path(dirpath)
            if len(here.relative_to(base).parts) >= depth:
                dirnames.clear()
            for name in filenames:
                if len(entries) >= 500:
                    return _ok({"entries": entries})
                item = here / name
                entries.append(
                    {
                        "path": str(item.relative_to(root)).replace("\\", "/"),
                        "type": "file",
                        "size": item.stat().st_size,
                    }
                )
        return _ok({"entries": entries})

    @server.tool()
    def read_file(path: str, start_line: int = 1, end_line: int = 0) -> dict[str, Any]:
        """Read a text slice. Rejects binary and paths outside the worktree."""
        try:
            target = resolve_inside(root, path)
        except PathGuardError as exc:
            return _err(exc.code, str(exc))
        if not target.is_file():
            return _err("NOT_FOUND", path)
        raw = target.read_bytes()
        if len(raw) > Limits().max_file_bytes:
            return _err("TOO_LARGE", path)
        if b"\x00" in raw:
            return _err("BAD_ARGS", "binary file")
        lines = raw.decode("utf-8", errors="replace").splitlines()
        stop = end_line or len(lines)
        text = "\n".join(lines[max(start_line, 1) - 1 : stop])
        return _ok({"text": _tail(text), "truncated": len(text) > 6000})

    @server.tool()
    def search(pattern: str, glob: str = "*") -> dict[str, Any]:
        """Literal search. Caps the number of matches."""
        matches: list[dict[str, str | int]] = []
        for path in root.rglob(glob):
            if ".git" in path.parts or not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for number, line in enumerate(lines, start=1):
                if pattern in line:
                    matches.append({"path": str(path.relative_to(root)).replace("\\", "/"), "line": number})
                    if len(matches) >= 100:
                        return _ok({"matches": matches})
        return _ok({"matches": matches})

    @server.tool()
    def run_check(name: str) -> dict[str, Any]:
        """Run one named check. The command is taken from the envelope, never from the model."""
        return execute_check(root, checks, name)

    @server.tool()
    def git_status() -> dict[str, Any]:
        """Short git status. Read-only."""
        proc = run_git(["status", "--porcelain"], cwd=root)
        if proc.returncode != 0:
            return _err("INTERNAL", proc.stderr.strip() or "git status failed")
        return _ok({"lines": [line for line in proc.stdout.splitlines() if line.strip()]})

    @server.tool()
    def git_diff(path: str | None = None) -> dict[str, Any]:
        """Unified diff. Read-only."""
        if path is not None:
            try:
                resolve_inside(root, path)
            except PathGuardError as exc:
                return _err(exc.code, str(exc))
        args = ["diff", "--", path] if path else ["diff"]
        proc = run_git(args, cwd=root)
        if proc.returncode != 0:
            return _err("INTERNAL", proc.stderr.strip() or "git diff failed")
        return _ok({"text": _tail(proc.stdout, 8000), "truncated": len(proc.stdout) > 8000})

    return server


def mcp_stdio() -> tuple[str, list[str]]:
    """The worker venv interpreter. A bare `python.exe` is the uv launcher and drops the venv."""
    return sys.executable, ["-m", "treefleet.mcp_servers.worktree_fs_server"]


def mcp_env(root: Path, checks: object) -> dict[str, str]:
    """Minimal child environment. Windows Python and asyncio hang without SystemRoot, TEMP and TMP."""
    env: dict[str, str] = {"PYTHONUNBUFFERED": "1"}
    for key in ("SystemRoot", "TEMP", "TMP", "PATH", "PATHEXT"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    if sys.platform == "win32":
        if "SystemRoot" not in env:
            fallback = os.environ.get("SYSTEMROOT")
            if not fallback:
                raise RuntimeError("MCP server environment is missing SystemRoot")
            env["SystemRoot"] = fallback
        for key in ("TEMP", "TMP"):
            if key not in env:
                raise RuntimeError(f"MCP server environment is missing {key}")
    env["TREEFLEET_FS_ROOT"] = str(root)
    env["TREEFLEET_FS_CHECKS"] = json.dumps(checks)
    return env


def handshake(root: Path, checks: object, timeout_s: float, python: Path | None = None) -> list[str]:
    """Start the filesystem server and return its tool names. A stuck process raises TimeoutError."""
    command = str(python) if python is not None else mcp_stdio()[0]
    proc = subprocess.Popen(
        [command, "-m", "treefleet.mcp_servers.worktree_fs_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=mcp_env(root, checks),
        cwd=root,
        shell=False,
    )
    deadline = time.monotonic() + timeout_s
    try:
        _send(
            proc,
            _rpc(
                "initialize",
                1,
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "treefleet", "version": "0.1.0"},
                },
            ),
        )
        started = _wait(proc, deadline, 1, timeout_s)
        if "error" in started:
            raise RuntimeError(f"MCP handshake failed: {started['error']}")
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(proc, _rpc("tools/list", 2, {}))
        listed = _wait(proc, deadline, 2, timeout_s)
        if "error" in listed:
            raise RuntimeError(f"MCP handshake failed: {listed['error']}")
        tools = listed.get("result", {}).get("tools", [])
        return [str(item["name"]) for item in tools if isinstance(item, dict) and "name" in item]
    except TimeoutError:
        raise TimeoutError(f"MCP server did not complete the handshake within {timeout_s}s") from None
    finally:
        if proc.poll() is None:
            kill_tree(proc.pid)


def _rpc(method: str, call_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": call_id, "method": method, "params": params}


def _send(proc: subprocess.Popen[bytes], message: dict[str, Any]) -> None:
    if proc.stdin is None:
        raise RuntimeError("MCP server stdin is closed")
    proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
    proc.stdin.flush()


def _wait(proc: subprocess.Popen[bytes], deadline: float, call_id: int, timeout_s: float) -> dict[str, Any]:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"MCP server did not complete the handshake within {timeout_s}s")
        line = _readline(proc, remaining)
        if not line.strip():
            raise RuntimeError("MCP server closed stdout before the handshake finished")
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError("MCP server wrote non-JSON to stdout") from exc
        if not isinstance(message, dict):
            raise RuntimeError("MCP server wrote non-JSON to stdout")
        if message.get("id") == call_id:
            return message


def _readline(proc: subprocess.Popen[bytes], timeout_s: float) -> str:
    if proc.stdout is None:
        raise RuntimeError("MCP server stdout is closed")
    found: list[bytes] = []

    def read() -> None:
        found.append(proc.stdout.readline() if proc.stdout is not None else b"")

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive():
        raise TimeoutError(f"MCP server did not complete the handshake within {timeout_s}s")
    return found[0].decode("utf-8", errors="replace") if found else ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=os.environ.get("TREEFLEET_FS_ROOT"))
    parser.add_argument("--checks", default=os.environ.get("TREEFLEET_FS_CHECKS", "{}"))
    args = parser.parse_args()
    if not args.root:
        raise SystemExit("TREEFLEET_FS_ROOT is required")
    build_fs_server(Path(args.root), json.loads(args.checks)).run(transport="stdio")


if __name__ == "__main__":
    main()
