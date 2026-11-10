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
    by_alias = await call({"path": "", "file_path": "notes.txt"})
    alias_data = by_alias.get("data")
    assert by_alias["ok"] is True and isinstance(alias_data, dict) and "hello" in str(alias_data.get("text"))
    schema = next(tool.input_schema for tool in listed if tool.name == "read_file")
    required = schema.get("required", []) if isinstance(schema, dict) else list(schema.required or [])
    assert "path" in required
    assert "file_path" not in required
    search_schema = next(tool.input_schema for tool in listed if tool.name == "search")
    search_required = (
        search_schema.get("required", []) if isinstance(search_schema, dict) else list(search_schema.required or [])
    )
    properties = search_schema.get("properties", {}) if isinstance(search_schema, dict) else search_schema.properties
    assert "pattern" in search_required
    assert "glob" not in search_required
    assert "glob" in properties
    missing = await call({"path": ""})
    error = missing.get("error")
    assert missing["ok"] is False and isinstance(error, dict)
    assert error.get("message") == "read_file requires path (str)"
    blank_search = await server.call_tool("search", {"pattern": ""})
    assert isinstance(blank_search, CallToolResult)
    search_body = blank_search.structured_content
    assert isinstance(search_body, dict)
    search_error = search_body.get("error")
    assert isinstance(search_error, dict) and search_error.get("message") == "search requires pattern (str)"

    sink = EventSink(tmp_path)
    on_tool_result(json.dumps(missing), sink, "abc12345", "alpha", tool="read_file")
    on_tool_result(_VALIDATION, sink, "abc12345", "alpha")
    on_tool_result(
        json.dumps(missing),
        sink,
        "abc12345",
        "alpha",
        tool="read_file",
        args={"path": "", "note": "sk-abcdefghij" + ("x" * 220)},
    )
    text = (tmp_path / "abc12345" / "events.jsonl").read_text(encoding="utf-8")
    assert "guard.decision" not in text
    assert "tool.error" not in text
    rows = [json.loads(line) for line in text.splitlines()]
    assert [row["type"] for row in rows] == ["tool.arg_error", "tool.arg_error", "tool.arg_error"]
    assert rows[0]["payload"] == {"tool": "read_file", "message": "read_file requires path (str)"}
    assert rows[1]["payload"]["tool"] == "read_file"
    assert rows[1]["payload"]["message"] == "read_file requires path (str)"
    assert rows[2]["payload"]["arguments"]["path"] == ""
    assert "sk-abcdefghij" not in text
    assert len(rows[2]["payload"]["arguments"]["note"]) <= 200
