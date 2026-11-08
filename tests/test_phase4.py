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

from treefleet.checks import detect_checks
from treefleet.cli import main
from treefleet.config import load_settings
from treefleet.contracts import IsolationReport, Plan, RunRequest, StateKey, WorkerResult, WorkerTaskEnvelope
from treefleet.events import EventSink
from treefleet.git.cursor_scope import ScopeViolation
from treefleet.git.worktrees import kill_tree, list_worktrees, remove_worktree
from treefleet.mcp_servers.worktree_fs_server import execute_check
from treefleet.orchestrator.agents import assign_checks, plan_goal, plan_problems, scan_text
from treefleet.orchestrator.pipeline import OfflineFakes, ask_plan, cleanup_run, run_pipeline
from treefleet.worker.spawner import before_spawn, worker_env, worker_pids

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
    mock: str = "1",
):
    _secrets(monkeypatch)
    llm, calls = _scripted(replies)
    outcome = await run_pipeline(
        RunRequest(goal=GOAL, repo=str(repo), plan_approval=approval),
        load_settings(ROOT / "treefleet.toml"),
        approve=approve,
        fakes=OfflineFakes(planner=llm, cursor_cli=FAKE, fake_sleep=sleep, kill_task=kill, mock_llm=mock),
        runs_root=tmp_path / "runs",
        worktree_root=WT,
        port_start=_port(),
    )
    return outcome, calls


def test_ask_plan_uses_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = Plan.model_validate_json(_plan_json(["alpha"]))
    monkeypatch.setattr("builtins.input", lambda prompt: "n" if "Approve plan?" in prompt else "")
    assert ask_plan(plan) is False


async def test_planner_accepts_low_medium_high() -> None:
    raw = json.dumps(
        {
            "goal": GOAL,
            "rationale": "Split the work.",
            "tasks": [
                {
                    "id": task_id,
                    "title": f"Edit {task_id}",
                    "instructions": "Add one sentence to README.md.",
                    "allowed_paths": ["README.md"],
                    "complexity": word,
                }
                for task_id, word in (("alpha", "low"), ("beta", "medium"), ("gamma", "high"))
            ],
        }
    )
    llm, calls = _scripted([raw])
    plan = await plan_goal(llm, GOAL, "README.md")
    assert calls["n"] == 1
    assert [task.complexity for task in plan.tasks] == ["trivial", "moderate", "complex"]


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
        if not isolation.ok:
            detail = [
                item
                for item in (tmp_path / "runs" / outcome.run_id / "events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if "isolation.proof_detail" in item
            ]
            print("\n".join(detail), flush=True)
        assert isolation.ok, [
            (
                item.task_id,
                item.process_cwd_matches,
                item.toplevel_matches,
                item.registered_in_worktree_list,
                item.canary_absent,
                item.changes_within_allowed_paths,
                [attempt.model_dump(mode="json") for attempt in item.cwd_attempts],
            )
            for item in isolation.tasks
        ]
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


def _cpu_burners(count: int = 4) -> list[subprocess.Popen[bytes]]:
    code = (
        "import time\n"
        "end = time.time() + 180\n"
        "while time.time() < end:\n"
        "    x = 0\n"
        "    while x < 100000:\n"
        "        x += 1\n"
    )
    return [
        subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        for _ in range(count)
    ]


async def test_isolation_holds_under_cpu_stress(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Five fleet runs while four processes burn CPU. A cwd failure prints the proof detail."""
    burners = _cpu_burners()
    try:
        for index in range(5):
            repo = tmp_path / f"repo{index}"
            _git(repo)
            outcome = None
            try:
                outcome, _calls = await _run(
                    repo,
                    [_plan_json(["alpha", "beta", "gamma"], override="composer-2.5-fast")],
                    monkeypatch,
                    tmp_path,
                    sleep="0.2",
                )
                assert outcome.code == 0, outcome.message
                assert outcome.report is not None
                isolation = IsolationReport.model_validate(outcome.state[StateKey.ISOLATION])
                events = [
                    json.loads(line)
                    for line in (tmp_path / "runs" / outcome.run_id / "events.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                    if line.strip()
                ]
                detail = [item for item in events if item.get("type") == "isolation.proof_detail"]
                if not isolation.ok:
                    print(json.dumps(detail, indent=2), flush=True)
                assert isolation.ok, detail
                ready = [item["ts"] for item in events if item.get("type") == "worker.ready"]
                spawned = [item["ts"] for item in events if item.get("type") == "worker.spawned"]
                assert len(ready) == 3 and len(spawned) == 3
            finally:
                if outcome is not None:
                    cleanup_run(repo, outcome.run_id)
                _sweep(repo)
    finally:
        for proc in burners:
            kill_tree(proc.pid)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


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


def test_long_notes_are_clipped_to_the_draft_limit() -> None:
    from treefleet.worker.draft import parse_draft

    draft = parse_draft(json.dumps({"notes": "x" * 3001}))
    assert len(draft.notes) == 3000
    assert draft.notes == "x" * 3000


def test_file_list_uses_the_base_sha_across_two_commits(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    (repo / "extra.py").write_text("x\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "extra.py"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "commit", "-m", "extra"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "branch", "demo"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "reset", "--hard", "HEAD~1"], cwd=repo, check=True, shell=False)
    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-b", "tf/file01/alpha", str(worktree), "demo"],
        cwd=repo,
        check=True,
        shell=False,
    )
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=worktree, capture_output=True, text=True, shell=False, check=True
    ).stdout.strip()
    (worktree / "README.md").write_text("base\nmore\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "README.md"], cwd=worktree, check=True, shell=False)
    subprocess.run(["git", "commit", "-m", "readme"], cwd=worktree, check=True, shell=False)
    (worktree / "notes.txt").write_text("second commit\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "notes.txt"], cwd=worktree, check=True, shell=False)
    subprocess.run(["git", "commit", "-m", "notes"], cwd=worktree, check=True, shell=False)
    code = (
        "import json, os\n"
        "from pathlib import Path\n"
        "from treefleet.worker.crew import _files\n"
        "print(json.dumps([item.path for item in _files(Path(os.environ['WT']), os.environ['BASE'])]))\n"
    )
    proc = subprocess.run(
        [str(WORKER), "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        shell=False,
        env={**os.environ, "WT": str(worktree), "BASE": base},
        check=False,
    )
    assert proc.returncode == 0, (proc.stdout[-1500:] + proc.stderr[-1500:]).strip()
    assert sorted(json.loads(proc.stdout.strip().splitlines()[-1])) == ["README.md", "notes.txt"]


async def test_empty_upstream_cancels_the_dependent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    plan = json.dumps(
        {
            "goal": GOAL,
            "rationale": "A task that changes nothing cancels the writer behind it.",
            "tasks": [
                {
                    "id": "review",
                    "title": "Note patterns",
                    "instructions": "Describe the repository layout in notes.",
                    "allowed_paths": ["notes.txt"],
                },
                {
                    "id": "write",
                    "title": "Write",
                    "instructions": "Add one sentence to README.md.",
                    "allowed_paths": ["README.md"],
                    "depends_on": ["review"],
                },
            ],
        }
    )
    outcome = None
    try:
        outcome, _calls = await _run(repo, [plan], monkeypatch, tmp_path, sleep="0", mock="long")
        assert outcome.report is not None, outcome.message
        by_id = {item.task_id: item for item in outcome.report.results}
        assert by_id["review"].status == "failed"
        assert by_id["review"].error is not None
        assert by_id["review"].error.startswith("worker made no changes")
        assert "shim edit" in by_id["review"].error
        assert by_id["write"].status == "failed"
        assert by_id["write"].error == "upstream review rejected"
        assert by_id["write"].commit_made is False
        events = [
            json.loads(line)
            for line in (tmp_path / "runs" / outcome.run_id / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        spawned = [event["task_id"] for event in events if event["type"] == "worker.spawned"]
        assert spawned == ["review"]
        reviewed = [
            event["task_id"] for event in events if event["type"] == "review_started" and event["task_id"] == "review"
        ]
        assert reviewed == []
        integration = outcome.report.integration
        assert integration is not None
        assert "write" not in integration.merged
        assert "review" not in integration.merged
    finally:
        if outcome is not None:
            cleanup_run(repo, outcome.run_id)
        _sweep(repo)


def _inspect_plan() -> str:
    return json.dumps(
        {
            "goal": GOAL,
            "rationale": "Look first.",
            "tasks": [
                {
                    "id": "inspect-modules",
                    "title": "Inspect the modules",
                    "instructions": "Inspect the repository and report the gaps.",
                    "allowed_paths": ["README.md"],
                }
            ],
        }
    )


def test_plan_guard_flags_only_a_leading_verb() -> None:
    plan = Plan.model_validate_json(_plan_json(["alpha"]))
    assert plan_problems(plan) is None
    raw = json.loads(_plan_json(["alpha"]))
    raw["tasks"][0]["title"] = "Run the unit tests"
    raw["tasks"][0]["instructions"] = "Add one sentence to README.md now."
    assert "run" in (plan_problems(Plan.model_validate(raw)) or "")
    raw["tasks"][0]["title"] = "Add unit tests"
    raw["tasks"][0]["instructions"] = "Add tests and then inspect the names."
    assert plan_problems(Plan.model_validate(raw)) is None


async def test_plan_guard_replans_once_with_the_reason() -> None:
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    seen: list[str] = []

    class Scripted(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):  # noqa: ANN001
            chunks: list[str] = []
            for content in getattr(llm_request, "contents", []) or []:
                for part in getattr(content, "parts", []) or []:
                    text = getattr(part, "text", None)
                    if text:
                        chunks.append(text)
            seen.append("\n".join(chunks))
            reply = _inspect_plan() if len(seen) == 1 else _plan_json(["alpha"])
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=reply)]))

    plan = await plan_goal(Scripted(model="scripted"), GOAL, "README.md")
    assert plan.tasks[0].id == "alpha"
    assert len(seen) == 2
    assert "starts with inspect" in seen[1]


async def test_plan_guard_failure_is_a_clear_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    outcome, _calls = await _run(repo, [_inspect_plan(), _inspect_plan()], monkeypatch, tmp_path, sleep="0")
    assert outcome.code == 1
    assert outcome.message.startswith("plan rejected:")
    assert "inspect" in outcome.message
    assert outcome.report is None
    assert not any(item.branch and item.branch.startswith("tf/") for item in list_worktrees(repo))


def test_empty_check_ids_follow_the_edited_file() -> None:
    checks = {
        "pip-install-pytest": "python -m pip install pytest",
        "pytest": "python -m pytest -q",
        "npm-test": "npm test",
    }
    readme = Plan.model_validate_json(_plan_json(["alpha"]))
    assert assign_checks(readme, checks).tasks[0].check_ids == []
    raw = json.loads(_plan_json(["alpha"]))
    raw["tasks"][0]["allowed_paths"] = ["tests/test_notifications.py"]
    py_plan = assign_checks(Plan.model_validate(raw), checks)
    assert py_plan.tasks[0].check_ids == ["pytest"]
    raw["tasks"][0]["allowed_paths"] = ["src/client/types.ts"]
    raw["tasks"][0]["check_ids"] = ["npm-test"]
    kept = assign_checks(Plan.model_validate(raw), checks)
    assert kept.tasks[0].check_ids == ["npm-test"]


def _ci_repo(repo: Path) -> None:
    workflow = repo / ".github" / "workflows"
    workflow.mkdir(parents=True)
    (workflow / "ci.yml").write_text(
        "jobs:\n  test:\n    steps:\n"
        "      - run: python -m pip install pytest\n"
        "      - run: python -m pytest -q\n"
        "      - run: npm test\n",
        encoding="utf-8",
    )
    (repo / "package.json").write_text(
        json.dumps(
            {"scripts": {"test": "node --test tests/client.test.ts", "lint": "echo lint", "build": "echo skip"}}
        ),
        encoding="utf-8",
    )
    (repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (repo / "ruff.toml").write_text("line-length = 100\n", encoding="utf-8")


def test_repo_scan_detects_ci_package_and_tool_config(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    _ci_repo(repo)
    found = detect_checks(repo)
    assert found["pytest"] == "python -m pytest -q"
    assert found["pip-install-pytest"] == "python -m pip install pytest"
    assert found["npm-test"] == "npm test"
    assert found["test"] == "node --test tests/client.test.ts"
    assert found["lint"] == "echo lint"
    assert found["ruff"] == "ruff check"
    assert "build" not in found
    assert "python -m pytest -q" in scan_text(repo)


def test_envelope_records_the_run_base_sha() -> None:
    bare = {
        "run_id": "ab12cd",
        "task": {
            "id": "alpha",
            "title": "Edit readme",
            "instructions": "Update README.md with a short note.",
            "allowed_paths": ["README.md"],
        },
        "model": "deepseek/deepseek-flash",
    }
    assert WorkerTaskEnvelope.model_validate(bare).base_sha is None
    sha = "a" * 40
    assert WorkerTaskEnvelope.model_validate({**bare, "base_sha": sha}).base_sha == sha


def test_npm_resolves_to_node_without_a_shell() -> None:
    from treefleet.checks import command_argv

    argv = command_argv("npm test")
    assert argv[0].lower().endswith("node.exe")
    assert argv[1].lower().endswith("npm-cli.js")
    assert argv[2:] == ["test"]


def test_run_check_finds_python_and_node_without_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    from treefleet.checks import check_env

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-secret-value")
    env = check_env()
    assert env.get("PATH")
    assert "DEEPSEEK_API_KEY" not in env
    assert "CURSOR_API_KEY" not in env
    py = "python -c \"import os,sys; sys.exit(3 if 'DEEPSEEK_API_KEY' in os.environ else 0)\""
    result = execute_check(tmp_path, {"py": py}, "py")
    assert result["ok"] and result["data"]["passed"], result
    node = shutil.which("node")
    assert node
    script = "process.exit(process.env.DEEPSEEK_API_KEY ? 3 : 0)"
    node_result = execute_check(tmp_path, {"node": f'node -e "{script}"'}, "node")
    assert node_result["ok"] and node_result["data"]["passed"], node_result


def _cli(monkeypatch: pytest.MonkeyPatch) -> None:
    _secrets(monkeypatch)
    monkeypatch.setattr("treefleet.cli.require_known", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("treefleet.cli.require_tiers", lambda *_args, **_kwargs: None)

    def _stop(*_args, **_kwargs):  # noqa: ANN002
        from treefleet.orchestrator.pipeline import RunOutcome

        return RunOutcome(0, None, {}, "")

    monkeypatch.setattr("treefleet.cli.run_blocking", _stop)
    monkeypatch.chdir(ROOT)


def _payload(text: str) -> dict:
    return json.loads(next(line for line in text.splitlines() if line.startswith("{")))


def test_setup_commands_are_never_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from treefleet.checks import is_setup, select_checks

    assert is_setup("python -m pip install pytest")
    assert is_setup("npm ci")
    assert is_setup("npm install")
    assert is_setup("uv sync")
    assert not is_setup("npm test")
    assert not is_setup("python -m pytest -q")
    repo = tmp_path / "repo"
    _git(repo)
    ran = tmp_path / "ran.txt"
    target = str(ran).replace("\\", "/")
    py = sys.executable
    calls: list[list[str]] = []
    real_run = __import__("subprocess").run

    def _spy(argv, *args, **kwargs):  # noqa: ANN001
        if isinstance(argv, list):
            calls.append([str(part) for part in argv])
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr("treefleet.checks.subprocess.run", _spy)
    selected = select_checks(
        repo,
        {
            "pip-install-pytest": "python -m pip install pytest",
            "npm-ci": "npm ci",
            "ok": f"{py} -c \"open(r'{target}', 'w').write('1')\"",
            "bad": f'{py} -c "import sys; raise SystemExit(3)"',
        },
        worktree_root=Path(r"C:\tf"),
    )
    assert "pip-install-pytest" not in selected.checks
    assert "npm-ci" not in selected.checks
    assert "ok" in selected.checks
    assert "bad" not in selected.checks
    assert ran.is_file()
    assert any("setup" in note and "not run" in note for note in selected.notes)
    assert any(note.startswith("dropped bad:") for note in selected.notes)
    assert not any("pip" in part for argv in calls for part in argv)
    assert not any(item.branch == "tf/probe01/probe" for item in list_worktrees(repo))


def test_python_resolution_order(tmp_path: Path) -> None:
    from treefleet.checks import resolve_python, rewrite_python

    repo = tmp_path / "repo"
    repo.mkdir()
    venv_py = repo / ".venv" / "Scripts" / "python.exe"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_bytes(b"")
    flag = tmp_path / "flag-python.exe"
    flag.write_bytes(b"")
    path_py = tmp_path / "path-python.exe"
    path_py.write_bytes(b"")
    tried: list[Path] = []

    def _none(exe: Path) -> bool:
        tried.append(exe)
        return False

    found, source = resolve_python(repo, flag, imports=_none, path_exes=[path_py])
    assert found is None and source == ""
    assert tried == [flag, venv_py, path_py, Path(sys.executable)]
    tried.clear()

    def _venv(exe: Path) -> bool:
        tried.append(exe)
        return exe == venv_py

    found, source = resolve_python(repo, None, imports=_venv, path_exes=[path_py])
    assert found == venv_py and source == ".venv"
    assert tried == [venv_py]
    py = Path(r"D:\py\python.exe")
    assert rewrite_python("python -m pytest -q", py) == r"D:\py\python.exe -m pytest -q"
    assert rewrite_python("npm test", py) == "npm test"
    assert rewrite_python(r"C:\other\python.exe -m pytest -q", py) == r"C:\other\python.exe -m pytest -q"


def test_worker_env_keeps_comspec_so_npm_can_run_scripts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMSPEC", r"C:\WINDOWS\system32\cmd.exe")
    env = worker_env("token")
    assert env.get("COMSPEC") == r"C:\WINDOWS\system32\cmd.exe"


def test_check_env_prepends_the_interpreter_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    from treefleet.checks import check_env

    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("CURSOR_API_KEY", "cursor-secret-value")
    monkeypatch.setenv("TREEFLEET_PYTHON_DIR", r"D:\py")
    env = check_env()
    assert env["PATH"].startswith("D:\\py" + os.pathsep)
    assert "DEEPSEEK_API_KEY" not in env
    assert "CURSOR_API_KEY" not in env
    assert "TREEFLEET_PYTHON_DIR" not in env


def _probe_repo(repo: Path) -> None:
    _git(repo)
    workflow = repo / ".github" / "workflows"
    workflow.mkdir(parents=True)
    (workflow / "ci.yml").write_text(
        "jobs:\n  test:\n    steps:\n"
        "      - run: python -m pip install pytest\n"
        "      - run: python -c \"open('kept.txt','w').write('1')\"\n"
        '      - run: python -c "import sys; raise SystemExit(3)"\n',
        encoding="utf-8",
    )


def test_cli_asks_before_using_detected_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    _probe_repo(repo)
    _cli(monkeypatch)
    prompts: list[str] = []

    def _answer(prompt: str) -> str:
        prompts.append(prompt)
        return "n"

    monkeypatch.setattr("builtins.input", _answer)
    assert main([GOAL, "--repo", str(repo), "--no-approval"]) == 0
    out = capsys.readouterr().out
    assert prompts == ["Use these checks? [Y/n] "]
    assert "not run" in out
    assert "dropped " in out
    assert _payload(out)["checks"] == {}


def test_cli_yes_no_checks_and_explicit_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    _probe_repo(repo)
    _cli(monkeypatch)

    def _refuse(_prompt: str) -> str:
        raise AssertionError("question should be skipped")

    monkeypatch.setattr("builtins.input", _refuse)
    assert main([GOAL, "--repo", str(repo), "--yes", "--no-approval"]) == 0
    yes_out = capsys.readouterr().out
    yes = _payload(yes_out)
    assert "pip install" not in " ".join(yes["checks"].values())
    assert any(
        "open('kept.txt','w').write('1')" in command and not command.startswith("python ")
        for command in yes["checks"].values()
    )
    assert "not run" in yes_out
    assert "(" in yes_out and "python:" in yes_out
    assert main([GOAL, "--repo", str(repo), "--no-checks", "--no-approval"]) == 0
    assert _payload(capsys.readouterr().out)["checks"] == {}
    ok = f'{sys.executable} -c "import sys; raise SystemExit(0)"'
    assert main([GOAL, "--repo", str(repo), "--no-checks", "--check", f"pyok={ok}"]) == 0
    explicit = _payload(capsys.readouterr().out)
    assert explicit["checks"] == {"pyok": ok}
