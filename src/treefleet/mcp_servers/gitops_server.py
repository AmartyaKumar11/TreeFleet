"""Read-only git tools for the reviewer. Worktree add, merge and cleanup are not tools."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict, model_validator

from treefleet.contracts import Limits
from treefleet.git.proofs import collect_changes, in_scope
from treefleet.git.worktrees import GitError, git_out, list_worktrees, run_git

ErrorCode = Literal[
    "PATH_ESCAPE",
    "PATH_DENIED",
    "NOT_FOUND",
    "TOO_LARGE",
    "NOT_ALLOWED",
    "TIMEOUT",
    "BAD_ARGS",
    "INTERNAL",
]

READ_ONLY_TOOLS = frozenset({"worktree_list", "branch_summary", "diff", "show_file", "scope_check"})
_DRIVE = re.compile(r"^[A-Za-z]:")


class ToolErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: ErrorCode
    message: str


class ToolResult(BaseModel):
    """Common MCP result from CONTRACTS.md section 2. Not a cross-process payload in contracts.py."""

    model_config = ConfigDict(extra="ignore")
    ok: bool
    data: dict[str, Any] | None = None
    error: ToolErrorBody | None = None

    @model_validator(mode="after")
    def _paired(self) -> ToolResult:
        if self.ok and self.error is not None:
            raise ValueError("ok result cannot carry an error")
        if not self.ok and self.error is None:
            raise ValueError("failed result needs a typed error")
        return self


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return ToolResult(ok=True, data=data).model_dump()


def _err(code: ErrorCode, message: str) -> dict[str, Any]:
    return ToolResult(ok=False, error=ToolErrorBody(code=code, message=message)).model_dump()


def _unsafe(path: str) -> bool:
    raw = path.replace("\\", "/")
    if raw.startswith("/") or _DRIVE.match(raw):
        return True
    return any(part == ".." for part in raw.split("/"))


def build_gitops_server(
    repo: Path,
    base_ref: str,
    tasks: dict[str, tuple[list[str], list[str]]],
) -> MCPServer:
    server: MCPServer[Any] = MCPServer("gitops-mcp")

    @server.tool()
    def worktree_list() -> dict[str, Any]:
        """Porcelain worktree list as structured entries."""
        entries = [{"path": str(item.path), "head": item.head, "branch": item.branch} for item in list_worktrees(repo)]
        return _ok({"entries": entries})

    @server.tool()
    def branch_summary(branch: str) -> dict[str, Any]:
        """Commits and file stats on branch since the base ref."""
        if run_git(["rev-parse", "--verify", "--quiet", branch], cwd=repo).returncode != 0:
            return _err("NOT_FOUND", f"unknown branch {branch}")
        try:
            log = git_out(["log", "--format=%H%x09%s", f"{base_ref}..{branch}"], repo)
            numstat = git_out(["diff", "--numstat", f"{base_ref}...{branch}"], repo)
        except GitError as exc:
            return _err("INTERNAL", str(exc))
        commits = []
        for line in log.splitlines():
            sha, _, subject = line.partition("\t")
            if sha:
                commits.append({"sha": sha, "subject": subject})
        files = []
        for line in numstat.splitlines():
            added, deleted, path = line.split("\t", 2)
            files.append(
                {
                    "path": path,
                    "additions": 0 if added == "-" else int(added),
                    "deletions": 0 if deleted == "-" else int(deleted),
                }
            )
        return _ok({"commits": commits, "files": files})

    @server.tool()
    def diff(branch: str, path: str | None = None, max_chars: int = 8000) -> dict[str, Any]:
        """Unified diff of branch against the base ref, truncated to max_chars."""
        if path is not None and _unsafe(path):
            return _err("PATH_ESCAPE", path)
        if run_git(["rev-parse", "--verify", "--quiet", branch], cwd=repo).returncode != 0:
            return _err("NOT_FOUND", f"unknown branch {branch}")
        args = ["diff", f"{base_ref}...{branch}"]
        if path is not None:
            args.extend(["--", path])
        proc = run_git(args, cwd=repo)
        if proc.returncode != 0:
            return _err("INTERNAL", proc.stderr.strip() or "git diff failed")
        text = proc.stdout
        truncated = len(text) > max_chars
        return _ok({"text": text[:max_chars], "truncated": truncated})

    @server.tool()
    def show_file(ref: str, path: str) -> dict[str, Any]:
        """File text at a ref, capped at Limits.max_file_bytes."""
        if _unsafe(path):
            return _err("PATH_ESCAPE", path)
        proc = run_git(["show", f"{ref}:{path}"], cwd=repo)
        if proc.returncode != 0:
            return _err("NOT_FOUND", path)
        raw = proc.stdout.encode("utf-8")
        if len(raw) > Limits().max_file_bytes:
            return _err("TOO_LARGE", path)
        return _ok({"text": proc.stdout, "truncated": False})

    @server.tool()
    def scope_check(task_id: str) -> dict[str, Any]:
        """Files changed in the task worktree that fall outside allowed_paths."""
        scope = tasks.get(task_id)
        if scope is None:
            return _err("BAD_ARGS", f"unknown task {task_id}")
        allowed, denied = scope
        match = next(
            (item for item in list_worktrees(repo) if item.branch and item.branch.split("/")[-1] == task_id),
            None,
        )
        if match is None:
            return _err("NOT_FOUND", f"no worktree for {task_id}")
        try:
            changed = collect_changes(match.path, base_ref)
        except GitError as exc:
            return _err("INTERNAL", str(exc))
        outside = [path for path in changed if not in_scope(path, allowed, denied)]
        return _ok({"task_id": task_id, "outside": outside})

    return server
