"""Phase 4 addendum: short MCP tool names and Cursor config that must not run."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from treefleet.config import load_settings
from treefleet.contracts import IsolationReport, Plan, RunRequest, StateKey, WorkerResult
from treefleet.events import EventSink
from treefleet.git.cursor_scope import ScopeViolation
from treefleet.git.worktrees import list_worktrees, remove_worktree
from treefleet.orchestrator.agents import plan_goal
from treefleet.orchestrator.pipeline import OfflineFakes, ask_plan, cleanup_run, run_pipeline
from treefleet.worker.spawner import before_spawn, worker_pids

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


GOAL = "Add one sentence to README.md for each task."
WT = Path(r"C:\tf") if sys.platform == "win32" else Path("/tmp/tf")
FAKE = ROOT / "spikes" / "s9_fakes.py"


def _secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _plan_json(ids: list[str], depends: dict[str, list[str]] | None = None, override: str | None = None) -> str:
    tasks = []
    for task_id in ids:
        task = {
            "id": task_id,
            "title": f"Edit {task_id} readme",
            "instructions": f"Add one sentence to README.md for {task_id}.",
            "allowed_paths": ["README.md"],
            "depends_on": (depends or {}).get(task_id, []),
        }
        if override and task_id == ids[-1]:
            task["model_override"] = override
        tasks.append(task)
    return json.dumps(
        {
            "goal": GOAL,
            "rationale": "Split the work across isolated worktrees.",
            "tasks": tasks,
        }
    )


def _scripted(replies: list[str]):
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    cursor = {"n": 0}

    class Scripted(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):  # noqa: ANN001
            text = replies[min(cursor["n"], len(replies) - 1)]
            cursor["n"] += 1
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=text)]))

    return Scripted(model="scripted"), cursor


def _sweep(repo: Path) -> None:
    for item in list_worktrees(repo):
        if item.branch and item.branch.startswith("tf/"):
            remove_worktree(repo, item.path)


async def _run(
    repo: Path,
    replies: list[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    approval: bool = False,
    approve=None,
    sleep: str = "1",
    kill: str | None = None,
):
    _secrets(monkeypatch)
    llm, calls = _scripted(replies)
    outcome = await run_pipeline(
        RunRequest(goal=GOAL, repo=str(repo), plan_approval=approval),
        load_settings(ROOT / "treefleet.toml"),
        approve=approve,
        fakes=OfflineFakes(planner=llm, cursor_cli=FAKE, fake_sleep=sleep, kill_task=kill),
        runs_root=tmp_path / "runs",
        worktree_root=WT,
        port_start=_port(),
    )
    return outcome, calls


def test_ask_plan_uses_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = Plan.model_validate_json(_plan_json(["alpha"]))
    monkeypatch.setattr("builtins.input", lambda prompt: "n" if "Approve plan?" in prompt else "")
    assert ask_plan(plan) is False


async def test_planner_repairs_invalid_json_once() -> None:
    good = _plan_json(["alpha"])
    llm, calls = _scripted(["{", good])
    plan = await plan_goal(llm, GOAL, "README.md")
    assert plan.tasks[0].id == "alpha"
    assert calls["n"] == 2


async def test_declined_plan_creates_no_worktrees(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    outcome, _calls = await _run(
        repo, [_plan_json(["alpha"])], monkeypatch, tmp_path, approval=True, approve=lambda _plan: False
    )
    assert outcome.code == 0
    assert outcome.message == "plan declined"
    assert not any(item.branch and item.branch.startswith("tf/") for item in list_worktrees(repo))
    events = (tmp_path / "runs" / outcome.run_id / "events.jsonl").read_text(encoding="utf-8")
    assert "awaiting_approval" in events


async def test_dirty_checkout_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    _secrets(monkeypatch)
    outcome = await run_pipeline(
        RunRequest(goal=GOAL, repo=str(repo), plan_approval=False),
        load_settings(ROOT / "treefleet.toml"),
    )
    assert outcome.code == 2
    assert "dirty checkout" in outcome.message
    assert not any(item.branch and item.branch.startswith("tf/") for item in list_worktrees(repo))


async def test_three_workers_overlap_and_prove_isolation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    before = set(worker_pids())
    outcome = None
    try:
        outcome, _calls = await _run(
            repo,
            [_plan_json(["alpha", "beta", "gamma"], override="composer-2.5-fast")],
            monkeypatch,
            tmp_path,
            sleep="1.5",
        )
        assert outcome.code == 0, outcome.message
        assert outcome.report is not None
        assert set(outcome.state[StateKey.WORKER_RESULTS]) == {"alpha", "beta", "gamma"}
        for task_id in ("alpha", "beta", "gamma"):
            WorkerResult.model_validate(outcome.state[StateKey.WORKER_RESULTS][task_id])
        Plan.model_validate(outcome.state[StateKey.PLAN])
        isolation = IsolationReport.model_validate(outcome.state[StateKey.ISOLATION])
        assert isolation.ok
        assert len({item.worktree_path for item in isolation.tasks}) == 3
        assert len({item.branch for item in isolation.tasks}) == 3
        text = outcome.report.to_markdown()
        assert "composer-2.5" in text
        assert "composer-2.5-fast" in text
        events = [
            json.loads(line)
            for line in (tmp_path / "runs" / outcome.run_id / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        spawned = [event["ts"] for event in events if event["type"] == "worker.spawned"]
        exited = [event["ts"] for event in events if event["type"] == "worker.exited"]
        assert len(spawned) == 3 and len(exited) == 3
        assert max(spawned) < min(exited)
        assert set(worker_pids()) <= before
    finally:
        if outcome is not None:
            cleanup_run(repo, outcome.run_id)
        _sweep(repo)


async def test_killed_worker_fails_and_the_others_finish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    before = set(worker_pids())
    outcome = None
    try:
        outcome, _calls = await _run(
            repo,
            [_plan_json(["alpha", "beta", "gamma"])],
            monkeypatch,
            tmp_path,
            sleep="3",
            kill="beta",
        )
        assert outcome.report is not None
        by_id = {item.task_id: item for item in outcome.report.results}
        assert by_id["beta"].status == "failed"
        assert by_id["alpha"].status == "done"
        assert by_id["gamma"].status == "done"
        assert set(worker_pids()) <= before
    finally:
        if outcome is not None:
            cleanup_run(repo, outcome.run_id)
        _sweep(repo)


async def test_dependent_task_gets_the_upstream_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    outcome = None
    try:
        outcome, _calls = await _run(
            repo,
            [_plan_json(["alpha", "beta"], depends={"beta": ["alpha"]})],
            monkeypatch,
            tmp_path,
        )
        assert outcome.code == 0, outcome.message
        events = [
            json.loads(line)
            for line in (tmp_path / "runs" / outcome.run_id / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        alpha_exit = next(
            event["ts"] for event in events if event["type"] == "worker.exited" and event["task_id"] == "alpha"
        )
        beta_spawn = next(event for event in events if event["type"] == "worker.spawned" and event["task_id"] == "beta")
        assert alpha_exit < beta_spawn["ts"]
        alpha = WorkerResult.model_validate(outcome.state[StateKey.WORKER_RESULTS]["alpha"])
        assert alpha.summary in beta_spawn["payload"]["upstream"]
    finally:
        if outcome is not None:
            cleanup_run(repo, outcome.run_id)
        _sweep(repo)


async def test_cancelled_run_leaves_no_worker_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    before = set(worker_pids())
    task = asyncio.create_task(_run(repo, [_plan_json(["alpha"])], monkeypatch, tmp_path, sleep="4"))
    for _ in range(200):
        if set(worker_pids()) - before:
            break
        await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert set(worker_pids()) <= before
    _sweep(repo)
