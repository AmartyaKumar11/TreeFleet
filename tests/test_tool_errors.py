"""Tool-argument failures are tool.error events, not guard decisions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.types import CallToolResult

from treefleet.contracts import GuardDecision, GuardStage, policy_guard_count
from treefleet.events import EventSink
from treefleet.mcp_servers.worktree_fs_server import build_fs_server
from treefleet.worker.callbacks import on_tool_result

pytestmark = pytest.mark.phase5

_VALIDATION = (
    "Error executing tool read_file: 1 validation error for read_fileArguments "
    "path Field required [type=missing, input_value={}, input_type=dict]"
)


def test_policy_guard_count_skips_argument_failures() -> None:
    decisions = [
        GuardDecision(
            stage=GuardStage.TOOL_RESULT, allowed=False, detector="deterministic", reasons=["Field required"]
        ),
        GuardDecision(
            stage=GuardStage.TOOL_ARGS, allowed=False, detector="deterministic", reasons=["path rejected: parent"]
        ),
        GuardDecision(stage=GuardStage.INPUT, allowed=True, detector="deterministic"),
    ]
    assert policy_guard_count(decisions) == 2


async def test_read_file_path_alias_and_missing_argument(tmp_path: Path) -> None:
    root = tmp_path / "wt"
    root.mkdir()
    (root / "notes.txt").write_text("hello\n", encoding="utf-8")
    server = build_fs_server(root, {})
    listed = await server.list_tools()
    described = next(tool.description or "" for tool in listed if tool.name == "read_file")
    assert "file_path" in described and "alias" in described

    async def call(arguments: dict[str, object]) -> dict[str, object]:
        result = await server.call_tool("read_file", arguments)
        assert isinstance(result, CallToolResult)
        payload = result.structured_content
        assert isinstance(payload, dict)
        return payload

    by_path = await call({"path": "notes.txt"})
    path_data = by_path.get("data")
    assert by_path["ok"] is True and isinstance(path_data, dict) and "hello" in str(path_data.get("text"))
    by_alias = await call({"file_path": "notes.txt"})
    alias_data = by_alias.get("data")
    assert by_alias["ok"] is True and isinstance(alias_data, dict) and "hello" in str(alias_data.get("text"))
    missing = await call({})
    error = missing.get("error")
    assert missing["ok"] is False and isinstance(error, dict) and error.get("message") == "path is required"

    sink = EventSink(tmp_path)
    on_tool_result(json.dumps(missing), sink, "abc12345", "alpha", tool="read_file")
    on_tool_result(_VALIDATION, sink, "abc12345", "alpha")
    text = (tmp_path / "abc12345" / "events.jsonl").read_text(encoding="utf-8")
    assert "guard.decision" not in text
    rows = [json.loads(line) for line in text.splitlines()]
    assert [row["type"] for row in rows] == ["tool.error", "tool.error"]
    assert rows[0]["payload"] == {"tool": "read_file", "message": "path is required"}
    assert rows[1]["payload"]["tool"] == "read_file"
    assert "Field required" in rows[1]["payload"]["message"]
