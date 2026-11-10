"""Phase 10 step 2: the run envelope and, later in this file, session commands."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psutil
import pytest

from treefleet.cli import main
from treefleet.config import config_path, load_settings
from treefleet.contracts import RunRequest
from treefleet.git.worktrees import branch_name, list_worktrees, remove_worktree, run_git
from treefleet.orchestrator.pipeline import OfflineFakes, cleanup_run, run_pipeline
from treefleet.sessions import (
    HEARTBEAT_S,
    SECRET_ENV_NAMES,
    RunRecord,
    RunStore,
    atomic_write,
    cleanup_session,
    format_ls,
    kill_run,
    kill_task,
    list_runs,
    load_run,
    pid_matches,
    run_state,
)

pytestmark = pytest.mark.phase10

ROOT = Path(__file__).resolve().parents[1]
GOAL = "Add one sentence to README.md for each task."
WT = Path(r"C:\tf") if sys.platform == "win32" else Path("/tmp/tf")
FAKE = ROOT / "spikes" / "s9_fakes.py"


def _git(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "config", "user.email", "treefleet@local"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "config", "user.name", "TreeFleet"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=repo, check=True, shell=False)
    (repo / "README.md").write_text("base\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, shell=False)


def _plan(ids: list[str]) -> str:
    return json.dumps(
        {
            "goal": GOAL,
            "rationale": "Split the work across isolated worktrees.",
            "tasks": [
                {
                    "id": task_id,
                    "title": f"Edit {task_id} readme",
                    "instructions": f"Add one sentence to README.md for {task_id}.",
                    "allowed_paths": ["README.md"],
                    "depends_on": [],
                }
                for task_id in ids
            ],
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

    return Scripted(model="scripted")


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")
    monkeypatch.setenv("EXAMPLE_TOKEN", "example-token-value")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)


def _sweep(repo: Path) -> None:
    for item in list_worktrees(repo):
        if item.branch and item.branch.startswith("tf/"):
            remove_worktree(repo, item.path)


def _store(path: Path) -> RunStore:
    return RunStore.start(
        path,
        run_id="s10env01",
        repo="D:/repo",
        base_sha="abc123",
        goal="Add one sentence to README.md for each task.",
        routing_mode="router",
        pid=os.getpid(),
        started=time.time(),
    )


def test_atomic_write_keeps_the_old_file_when_replace_never_happens(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    store = _store(path)
    original = path.read_text(encoding="utf-8")
    load_run(path)
    atomic_write(path, "{not json", replace=False)
    assert path.read_text(encoding="utf-8") == original
    assert (path.parent / "run.json.tmp").is_file()
    assert store.record.run_id == "s10env01"


def test_heartbeat_interval_is_five_seconds_and_touch_moves_updated_at(tmp_path: Path) -> None:
    assert HEARTBEAT_S == 5
    path = tmp_path / "run.json"
    store = _store(path)
    first = load_run(path).updated_at
    time.sleep(1.1)
    store.touch()
    second = load_run(path).updated_at
    assert second != first
    store.start_heartbeat(interval=0.05)
    try:
        deadline = time.time() + 2.5
        while time.time() < deadline and load_run(path).updated_at == second:
            time.sleep(0.1)
    finally:
        store.stop()
    assert load_run(path).updated_at != second


def test_dead_needs_a_gone_orchestrator_and_a_stale_heartbeat(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    _store(path)
    fresh = load_run(path)
    assert run_state(fresh) == "running"
    stale = fresh.model_copy(
        update={
            "orchestrator_pid": 2**31 - 1,
            "orchestrator_started": 1.0,
            "updated_at": (datetime.now(UTC) - timedelta(seconds=30)).isoformat(timespec="seconds"),
            "stage": "fleet",
        }
    )
    assert run_state(stale) == "dead"
    recent = stale.model_copy(update={"updated_at": datetime.now(UTC).isoformat(timespec="seconds")})
    assert run_state(recent) == "running"
    assert pid_matches(os.getpid(), 1.0) is False


def test_run_json_has_no_secret_names_or_token_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")
    monkeypatch.setenv("EXAMPLE_TOKEN", "example-token-value")
    path = tmp_path / "run.json"
    store = _store(path)
    store.set_plan(
        {
            "goal": "Add one sentence to README.md for each task.",
            "tasks": [{"id": "alpha", "title": "Edit alpha"}],
        }
    )
    store.upsert_task("alpha", status="working", worker_pid=1, worker_started=1.0, branch="tf/s10env01/alpha")
    store.set_stage("done")
    text = path.read_text(encoding="utf-8")
    for name in SECRET_ENV_NAMES:
        assert name not in text, name
    for key, value in os.environ.items():
        if not value or not (key.endswith("_KEY") or key.endswith("_TOKEN")):
            continue
        if value in text:
            raise AssertionError(f"{key} value appeared in run.json")
    record = json.loads(text)
    assert record["plan"]["tasks"][0]["id"] == "alpha"
    assert record["tasks"][0]["status"] == "working"
    assert "env" not in record


def _assert_no_secrets(text: str) -> None:
    for name in SECRET_ENV_NAMES:
        assert name not in text, name
    for key, value in os.environ.items():
        if not value or not (key.endswith("_KEY") or key.endswith("_TOKEN")):
            continue
        if value in text:
            raise AssertionError(f"{key} value appeared in run.json")


async def test_a_stub_run_writes_the_envelope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    _secrets(monkeypatch)
    outcome = None
    try:
        outcome = await run_pipeline(
            RunRequest(goal=GOAL, repo=str(repo), plan_approval=False),
            load_settings(),
            fakes=OfflineFakes(
                planner=_scripted([_plan(["alpha"])]),
                cursor_cli=FAKE,
                fake_sleep="0",
                mock_llm="1",
            ),
            runs_root=tmp_path / "runs",
            worktree_root=WT,
            port_start=_port(),
        )
        assert outcome.code == 0, outcome.message
        path = tmp_path / "runs" / outcome.run_id / "run.json"
        text = path.read_text(encoding="utf-8")
        _assert_no_secrets(text)
        record = load_run(path)
        assert record.stage == "done"
        assert record.goal == GOAL
        assert record.base_sha
        assert record.plan is not None
        assert record.tasks[0].task_id == "alpha"
        assert record.tasks[0].verdict
        assert record.tasks[0].worktree
        assert record.orchestrator_pid == os.getpid()
    finally:
        if outcome is not None and outcome.run_id:
            cleanup_run(repo, outcome.run_id)
        _sweep(repo)


ORCH = ROOT / ".venv-orch" / "Scripts" / "python.exe"


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        return psutil.Process(pid).is_running()
    except psutil.Error:
        return False


def _descendants(run_id: str) -> list[int]:
    found: list[int] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        command = " ".join(proc.info.get("cmdline") or [])
        markers = ("treefleet.worker", "s9_fakes", "worktree_fs_server")
        if run_id in command and any(part in command for part in markers):
            found.append(int(proc.info["pid"]))
    return found


def _launch(tmp_path: Path, repo: Path, run_id: str, sleep: str) -> tuple[subprocess.Popen[str], Path]:
    script = tmp_path / "orch.py"
    script.write_text(
        "\n".join(
            [
                "import asyncio, json, sys",
                "from pathlib import Path",
                "from treefleet.config import load_settings",
                "from treefleet.contracts import RunRequest",
                "from treefleet.orchestrator.pipeline import OfflineFakes, run_pipeline",
                "from google.adk.models.base_llm import BaseLlm",
                "from google.adk.models.llm_response import LlmResponse",
                "from google.genai import types",
                "root, repo, runs, wt, run_id, port, sleep = sys.argv[1:]",
                "plan = json.dumps({'goal': 'Add one sentence to README.md for each task.',",
                "'rationale': 'Split the work across isolated worktrees.',",
                "'tasks': [{'id': name, 'title': 'Edit ' + name,",
                "'instructions': 'Add one sentence to README.md for ' + name + '.',",
                "'allowed_paths': ['README.md'], 'depends_on': []} for name in ('alpha', 'beta', 'gamma')]})",
                "class Scripted(BaseLlm):",
                "    async def generate_content_async(self, llm_request, stream=False):",
                "        yield LlmResponse(content=types.Content(role='model', parts=[types.Part(text=plan)]))",
                "async def main():",
                "    goal = 'Add one sentence to README.md for each task.'",
                "    fake = Path(root) / 'spikes' / 's9_fakes.py'",
                "    await run_pipeline(",
                "        RunRequest(goal=goal, repo=repo, plan_approval=False),",
                "        load_settings(),",
                "        fakes=OfflineFakes(planner=Scripted(model='scripted'),",
                "            cursor_cli=fake,",
                "            fake_sleep=sleep, mock_llm='1'),",
                "        runs_root=Path(runs), worktree_root=Path(wt), port_start=int(port), run_id=run_id)",
                "asyncio.run(main())",
            ]
        ),
        encoding="utf-8",
    )
    log = tmp_path / "orch.log"
    handle = log.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            str(ORCH),
            str(script),
            str(ROOT),
            str(repo),
            str(tmp_path / "runs"),
            str(WT),
            run_id,
            str(_port()),
            sleep,
        ],
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        env=os.environ.copy(),
        shell=False,
    )
    return proc, log


def _wait_workers(path: Path, count: int, timeout: float = 60) -> RunRecord:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if path.is_file():
            record = load_run(path)
            pids = [task.worker_pid for task in record.tasks if task.worker_pid]
            if len(pids) >= count:
                return record
        time.sleep(0.3)
    raise AssertionError(f"workers did not appear in {path}")


def _stop(pids: list[int]) -> None:
    for pid in pids:
        if _alive(pid):
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], check=False, shell=False, capture_output=True)


def test_pid_reuse_is_not_killed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "treefleet.toml").write_text(config_path().read_text(encoding="utf-8"), encoding="utf-8")
    _secrets(monkeypatch)
    path = tmp_path / "runs" / "s10reuse" / "run.json"
    store = RunStore.start(
        path,
        run_id="s10reuse",
        repo=str(tmp_path),
        base_sha="abc",
        goal="keep this process",
        routing_mode="router",
        pid=999999,
        started=1.0,
    )
    store.upsert_task(
        "alpha",
        title="alpha",
        worktree=str(WT / "s10reuse" / "alpha"),
        worker_pid=os.getpid(),
        worker_started=1.0,
    )
    result = kill_task(tmp_path / "runs", "s10reuse", "alpha", WT)
    assert result.code == 1
    assert "start time mismatch" in result.lines[0]
    assert _alive(os.getpid())
    assert load_run(path).tasks[0].reason != "killed by user"


def test_killing_the_orchestrator_kills_the_workers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if sys.platform != "win32":
        pytest.skip("job object is the Windows guarantee")
    repo = tmp_path / "repo"
    _git(repo)
    _secrets(monkeypatch)
    run_id = "s10kill1"
    proc, log = _launch(tmp_path, repo, run_id, "30")
    path = tmp_path / "runs" / run_id / "run.json"
    record = None
    try:
        deadline = time.time() + 180
        while time.time() < deadline:
            if path.is_file():
                loaded = load_run(path)
                if len([task.worker_pid for task in loaded.tasks if task.worker_pid]) >= 3:
                    record = loaded
                    break
            if proc.poll() is not None:
                break
            time.sleep(0.3)
        if record is None and path.is_file():
            loaded = load_run(path)
            if len([task.worker_pid for task in loaded.tasks if task.worker_pid]) >= 3:
                record = loaded
        if record is None:
            raw = log.read_text(encoding="utf-8")[-1500:] if log.is_file() else ""
            tail = "\n".join(
                line
                for line in raw.splitlines()
                if not any(word in line.upper() for word in ("KEY", "TOKEN", "BEARER", "SECRET"))
            )
            statuses = ""
            if path.is_file():
                loaded = load_run(path)
                statuses = " ".join(f"{task.task_id}={task.status}:{task.reason}" for task in loaded.tasks)
            raise AssertionError(f"workers were not listening; orchestrator={proc.poll()}; {statuses}; {tail}")
        worker_pids = [task.worker_pid for task in record.tasks if task.worker_pid]
        assert len(worker_pids) == 3
        subprocess.run(["taskkill", "/F", "/PID", str(record.orchestrator_pid)], check=False, shell=False)
        deadline = time.time() + 10
        while time.time() < deadline:
            if not any(_alive(pid) for pid in worker_pids) and not _descendants(run_id):
                break
            time.sleep(0.4)
        assert not any(_alive(pid) for pid in worker_pids), log.read_text(encoding="utf-8")[-1500:]
        assert _descendants(run_id) == []
        assert (WT / run_id / "alpha").is_dir()
        assert any(item.branch == branch_name(run_id, "alpha") for item in list_worktrees(repo))
        deadline = time.time() + 40
        shown = ""
        while time.time() < deadline:
            shown = format_ls(list_runs(tmp_path / "runs"))
            if run_id in shown and "dead" in shown:
                break
            time.sleep(0.5)
        assert f"{run_id}" in shown and "dead" in shown
    finally:
        if record is not None:
            _stop([record.orchestrator_pid, *[task.worker_pid or 0 for task in record.tasks]])
        if proc.poll() is None:
            proc.kill()
        cleanup_run(repo, run_id)
        for name in ("alpha", "beta", "gamma", "integrated"):
            run_git(["branch", "-D", branch_name(run_id, name)], cwd=repo)
        _sweep(repo)


def test_kill_task_fails_that_task_and_the_others_finish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    _secrets(monkeypatch)
    run_id = "s10kill2"
    proc, log = _launch(tmp_path, repo, run_id, "8")
    path = tmp_path / "runs" / run_id / "run.json"
    record = None
    try:
        record = _wait_workers(path, 3)
        result = kill_task(tmp_path / "runs", run_id, "beta", WT)
        assert result.code == 0, result.lines
        assert proc.wait(timeout=90) == 0, log.read_text(encoding="utf-8")[-1500:]
        final = load_run(path)
        by_id = {task.task_id: task for task in final.tasks}
        assert by_id["beta"].status == "failed"
        assert by_id["beta"].reason == "killed by user"
        assert by_id["alpha"].status == "done"
        assert by_id["gamma"].status == "done"
        events = (tmp_path / "runs" / run_id / "events.jsonl").read_text(encoding="utf-8")
        assert "worker.killed" in events
    finally:
        if proc.poll() is None:
            proc.kill()
        if record is not None:
            _stop([record.orchestrator_pid, *[task.worker_pid or 0 for task in record.tasks]])
        cleanup_run(repo, run_id)
        for name in ("alpha", "beta", "gamma", "integrated"):
            run_git(["branch", "-D", branch_name(run_id, name)], cwd=repo)
        _sweep(repo)


def test_kill_run_cancels_and_stops_every_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    _secrets(monkeypatch)
    run_id = "s10kill3"
    proc, log = _launch(tmp_path, repo, run_id, "30")
    path = tmp_path / "runs" / run_id / "run.json"
    record = None
    try:
        record = _wait_workers(path, 3)
        result = kill_run(tmp_path / "runs", run_id, WT)
        assert result.code == 0, result.lines
        assert not _alive(record.orchestrator_pid)
        assert not any(_alive(task.worker_pid) for task in record.tasks)
        assert load_run(path).stage == "cancelled"
    finally:
        if proc.poll() is None:
            proc.kill()
        if record is not None:
            _stop([record.orchestrator_pid, *[task.worker_pid or 0 for task in record.tasks]])
        cleanup_run(repo, run_id)
        for name in ("alpha", "beta", "gamma", "integrated"):
            run_git(["branch", "-D", branch_name(run_id, name)], cwd=repo)
        _sweep(repo)
        assert log.is_file()


def test_cleanup_is_a_dry_run_until_yes_and_then_idempotent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    run_id = "s10clean"
    root = WT / run_id
    root.mkdir(parents=True, exist_ok=True)
    worktree = root / "alpha"
    branch = branch_name(run_id, "alpha")
    integrated = branch_name(run_id, "integrated")
    run_git(["branch", branch], cwd=repo)
    run_git(["worktree", "add", str(worktree), branch], cwd=repo)
    (worktree / "README.md").write_text("changed\n", encoding="utf-8", newline="\n")
    run_git(["add", "README.md"], cwd=worktree)
    run_git(["commit", "-m", "alpha"], cwd=worktree)
    run_git(["merge", "--no-ff", "-m", "integrate", branch], cwd=repo)
    run_git(["branch", integrated], cwd=repo)
    runs = tmp_path / "runs"
    path = runs / run_id / "run.json"
    store = RunStore.start(
        path,
        run_id=run_id,
        repo=str(repo),
        base_sha="abc",
        goal="clean me",
        routing_mode="router",
        pid=999999,
        started=1.0,
    )
    store.upsert_task("alpha", title="alpha", branch=branch, worktree=str(worktree), status="done")
    try:
        dry = cleanup_session(run_id, repo=repo, runs=runs, worktree_root=WT)
        assert dry.code == 0
        assert any(line.startswith("worktree ") for line in dry.lines)
        assert any(line.startswith("branch ") for line in dry.lines)
        assert worktree.is_dir()
        assert run_git(["rev-parse", "--verify", branch], cwd=repo).returncode == 0
        done = cleanup_session(run_id, repo=repo, runs=runs, worktree_root=WT, yes=True)
        assert done.code == 0, done.lines
        assert not worktree.exists()
        assert run_git(["rev-parse", "--verify", branch], cwd=repo).returncode != 0
        again = cleanup_session(run_id, repo=repo, runs=runs, worktree_root=WT, yes=True)
        assert again.lines == ["nothing to clean"]
    finally:
        cleanup_run(repo, run_id)
        for name in (branch, integrated):
            run_git(["branch", "-D", name], cwd=repo)
        _sweep(repo)


def test_cleanup_refuses_an_unmerged_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    run_id = "s10unmrg"
    branch = branch_name(run_id, "alpha")
    run_git(["branch", branch], cwd=repo)
    run_git(["checkout", branch], cwd=repo)
    (repo / "README.md").write_text("unmerged\n", encoding="utf-8", newline="\n")
    run_git(["add", "README.md"], cwd=repo)
    run_git(["commit", "-m", "unmerged"], cwd=repo)
    run_git(["checkout", "main"], cwd=repo)
    try:
        refused = cleanup_session(run_id, repo=repo, runs=tmp_path / "runs", worktree_root=WT, yes=True)
        assert any("unmerged" in line for line in refused.lines)
        assert run_git(["rev-parse", "--verify", branch], cwd=repo).returncode == 0
        allowed = cleanup_session(
            run_id, repo=repo, runs=tmp_path / "runs", worktree_root=WT, yes=True, include_unmerged=True
        )
        assert allowed.code == 0, allowed.lines
        assert run_git(["rev-parse", "--verify", branch], cwd=repo).returncode != 0
    finally:
        run_git(["checkout", "main"], cwd=repo)
        run_git(["branch", "-D", branch], cwd=repo)


def test_cleanup_reads_the_repo_from_the_run_and_stops_when_repo_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    recorded = tmp_path / "recorded"
    other = tmp_path / "other"
    _git(recorded)
    _git(other)
    run_id = "s10repo1"
    branch = branch_name(run_id, "alpha")
    run_git(["branch", branch], cwd=recorded)
    runs = tmp_path / "runs"
    RunStore.start(
        runs / run_id / "run.json",
        run_id=run_id,
        repo=str(recorded),
        base_sha="abc",
        goal="clean the other repo",
        routing_mode="router",
        pid=999999,
        started=1.0,
    )
    try:
        dry = cleanup_session(run_id, repo=other, runs=runs, worktree_root=WT)
        assert dry.code == 0
        assert any(line == f"refuse {branch} unmerged" for line in dry.lines)
        assert run_git(["rev-parse", "--verify", branch], cwd=recorded).returncode == 0
        mismatch = cleanup_session(run_id, repo=other, runs=runs, worktree_root=WT, supplied=other)
        assert mismatch.code == 2
        assert mismatch.lines == [f"--repo {other}", f"run.json {recorded}"]
        assert run_git(["rev-parse", "--verify", branch], cwd=recorded).returncode == 0
        _secrets(monkeypatch)
        (tmp_path / "treefleet.toml").write_text(config_path().read_text(encoding="utf-8"), encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        assert main(["sessions", "cleanup", run_id, "--repo", str(other)]) == 2
        printed = capsys.readouterr().out
        assert str(other) in printed
        assert str(recorded) in printed
        assert run_git(["rev-parse", "--verify", branch], cwd=recorded).returncode == 0
    finally:
        run_git(["branch", "-D", branch], cwd=recorded)


def test_cli_lists_a_run_and_a_dead_board_says_dead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _git(tmp_path / "repo")
    _secrets(monkeypatch)
    (tmp_path / "treefleet.toml").write_text(config_path().read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "runs" / "s10dead1" / "run.json"
    store = RunStore.start(
        path,
        run_id="s10dead1",
        repo=str(tmp_path / "repo"),
        base_sha="abc",
        goal="show this dead run",
        routing_mode="router",
        pid=999999,
        started=1.0,
    )
    store.set_stage("fleet")
    stale = load_run(path).model_copy(
        update={"updated_at": (datetime.now(UTC) - timedelta(seconds=30)).isoformat(timespec="seconds")}
    )
    path.write_text(stale.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (path.parent / "events.jsonl").write_text(
        '{"run_id":"s10dead1","seq":0,"ts":"2026-11-08T00:00:00+00:00","source":"orchestrator","type":"worker.spawned","task_id":"alpha","payload":{}}\n',
        encoding="utf-8",
    )
    assert main(["sessions", "ls"]) == 0
    assert "dead" in capsys.readouterr().out
    from starlette.testclient import TestClient

    from treefleet.ui.server import create_app

    app = create_app(tmp_path / "repo", replay=path.parent / "events.jsonl", runs=tmp_path / "runs")
    page = TestClient(app, base_url="http://127.0.0.1").get("/").text
    assert ">DEAD<" in page
    assert ">REPLAY<" not in page
