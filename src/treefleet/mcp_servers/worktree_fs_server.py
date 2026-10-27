"""Read-only filesystem tools for one worktree. No write tool exists."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x, the version CrewAI pins
    from mcp.server.fastmcp import FastMCP as MCPServer  # pyright: ignore[reportAttributeAccessIssue]

from treefleet.contracts import CheckResult, Limits
from treefleet.git.worktrees import run_git

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
    """Argv whose sanitized form still ends with git_diff. A bare python.exe drops the venv."""
    return sys.executable, ["-m", "fs"]


def mcp_env(root: Path, checks: object) -> dict[str, str]:
    # CrewAI merges this onto the MCP default environment. Do not set
    # PYTHONIOENCODING: it puts stdio in text mode and the handshake never finishes.
    return {
        "TREEFLEET_FS_ROOT": str(root),
        "TREEFLEET_FS_CHECKS": json.dumps(checks),
    }


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
