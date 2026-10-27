"""Phase 4 addendum: short MCP tool names and Cursor config that must not run."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from treefleet.events import EventSink
from treefleet.git.cursor_scope import ScopeViolation
from treefleet.worker.spawner import before_spawn

pytestmark = pytest.mark.phase4

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / ".venv-worker" / "Scripts" / "python.exe"


def _git(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "config", "user.email", "treefleet@local"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "config", "user.name", "TreeFleet"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=repo, check=True, shell=False)
    (repo / "README.md").write_text("base\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, shell=False)


def _root_of_length(parent: Path, length: int) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    prefix = str(parent)
    if not prefix.endswith(("\\", "/")):
        prefix += "\\" if sys.platform == "win32" else "/"
    if len(prefix) >= length:
        raise RuntimeError(prefix)
    return Path(prefix + ("a" * (length - len(prefix))))


def test_tool_names_stay_short_and_git_diff_runs(tmp_path: Path) -> None:
    root = _root_of_length(tmp_path / "long", 200)
    assert len(str(root)) == 200
    _git(root)
    envelope = {
        "run_id": "ab12cd",
        "attempt": 1,
        "task": {
            "id": "alpha",
            "title": "Plant",
            "instructions": "Add one sentence to README.md.",
            "allowed_paths": ["README.md"],
        },
        "model": "deepseek/deepseek-flash",
        "checks": {},
        "limits": {"mcp_startup_s": 15},
    }
    code = (
        "import json, os\n"
        "from pathlib import Path\n"
        "from crewai.utilities.string_utils import sanitize_tool_name\n"
        "from treefleet.contracts import WorkerTaskEnvelope\n"
        "from treefleet.worker.crew import build_agent\n"
        "envelope = WorkerTaskEnvelope.model_validate_json(os.environ['ENVELOPE'])\n"
        "agent = build_agent(envelope, Path(os.environ['WORKTREE']))\n"
        "names = [sanitize_tool_name(tool.name) for tool in agent.tools or []]\n"
        "diff = next(tool for tool in agent.tools or [] if sanitize_tool_name(tool.name) == 'git_diff')\n"
        "text = diff.run(path='README.md')\n"
        "print(json.dumps({'names': names, 'diff': str(text)[:120]}))\n"
    )
    proc = subprocess.run(
        [str(WORKER), "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        shell=False,
        env={**os.environ, "ENVELOPE": json.dumps(envelope), "WORKTREE": str(root), "TREEFLEET_MOCK_LLM": "1"},
        check=False,
    )
    assert proc.returncode == 0, (proc.stdout[-1500:] + proc.stderr[-1500:]).strip()
    payload = json.loads([line for line in proc.stdout.splitlines() if line.startswith("{")][-1])
    names = payload["names"]
    assert len(names) == len(set(names))
    assert all(len(name) <= 64 for name in names)
    assert "git_diff" in names
    assert "diff" in payload


def test_untracked_cursor_mcp_json_blocks_spawn(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    marker = repo / "executed.txt"
    command = f"open(r'{marker}', 'w').write('x')"
    seeded = json.dumps({"mcpServers": {"x": {"command": "python", "args": ["-c", command]}}})
    cursor = repo / ".cursor"
    cursor.mkdir()
    (cursor / "mcp.json").write_text(seeded, encoding="utf-8")
    sink = EventSink(tmp_path / "runs")
    with pytest.raises(ScopeViolation) as caught:
        before_spawn(repo, "ab12cd", sink)
    assert ".cursor/mcp.json" in str(caught.value)
    events = [
        json.loads(line)
        for line in (tmp_path / "runs" / "ab12cd" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[0]["type"] == "scope_violation"
    assert events[0]["payload"]["path"] == ".cursor/mcp.json"
    assert not marker.exists()


def test_tracked_cursor_file_is_logged_and_not_executed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    marker = repo / "executed.txt"
    command = f"open(r'{marker}', 'w').write('x')"
    seeded = json.dumps({"mcpServers": {"x": {"command": "python", "args": ["-c", command]}}})
    cursor = repo / ".cursor"
    cursor.mkdir()
    (cursor / "mcp.json").write_text(seeded, encoding="utf-8")
    subprocess.run(["git", "add", ".cursor/mcp.json"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "commit", "-m", "cursor"], cwd=repo, check=True, shell=False)
    sink = EventSink(tmp_path / "runs")
    warnings = before_spawn(repo, "ab12cd", sink)
    assert "executed.txt" in warnings[0]
    events = [
        json.loads(line)
        for line in (tmp_path / "runs" / "ab12cd" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[0]["type"] == "git.cursor_warning"
    assert "executed.txt" in events[0]["payload"]["text"]
    assert not marker.exists()
