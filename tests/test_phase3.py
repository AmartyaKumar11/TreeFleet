"""Phase 3: read-only worktree tools, cursor runner, and the worker process."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import psutil
import pytest
from a2a.client import A2AClient
from a2a.types import (
    DataPart,
    Message,
    MessageSendParams,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskState,
)
from pydantic import ValidationError

from treefleet.contracts import CursorRunResult, CursorUsage, FileChange, OutputLine, WorkerDraft, WorkerResult
from treefleet.cursor_run import command_for, redact_line, run_cursor
from treefleet.events import EventSink
from treefleet.git.worktrees import create_worktree, kill_tree, prepare_repo, remove_worktree
from treefleet.mcp_servers.worktree_fs_server import PathGuardError, execute_check, mcp_stdio, resolve_inside
from treefleet.worker.draft import draft_with_retry
from treefleet.worker.facts import summary_from_files

pytestmark = pytest.mark.phase3

ROOT = Path(__file__).resolve().parents[1]
FAKE = ROOT / "spikes" / "s9_fakes.py"
WORKER = ROOT / ".venv-worker" / "Scripts" / "python.exe"


def test_cursor_run_result_reads_cli_usage() -> None:
    result = CursorRunResult.model_validate(
        {
            "exit_code": 0,
            "timed_out": False,
            "duration_s": 1.2,
            "files_changed": ["README.md"],
            "reverted_paths": [],
            "output_tail": "ok",
            "usage": {"inputTokens": 3, "outputTokens": 5, "cacheReadTokens": 1, "cacheWriteTokens": 0},
        }
    )
    assert result.usage == CursorUsage(inputTokens=3, outputTokens=5, cacheReadTokens=1, cacheWriteTokens=0)
    line = OutputLine(line=redact_line("sk-secretvalue " + ("x" * 300)))
    assert len(line.line) <= 200
    assert "sk-secretvalue" not in line.line
    with pytest.raises(ValidationError):
        OutputLine(line="y" * 201)


def test_path_guard_rejects_escapes(tmp_path: Path) -> None:
    root = tmp_path / "wt"
    root.mkdir()
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("no\n", encoding="utf-8")
    link = root / "escape"
    try:
        os.symlink(outside, link)
    except OSError:
        link = None
    cases = [
        "../outside.txt",
        str(outside),
        "C:/Windows/notepad.exe",
        ".git/config",
        "a\u200bb",
        "\uff0e\uff0e/secret",
        "x" * 241,
    ]
    for raw in cases:
        with pytest.raises(PathGuardError) as caught:
            resolve_inside(root, raw)
        assert caught.value.code in {"PATH_ESCAPE", "PATH_DENIED"}
    if link is not None:
        with pytest.raises(PathGuardError) as caught:
            resolve_inside(root, "escape")
        assert caught.value.code == "PATH_ESCAPE"
    assert resolve_inside(root, "README.md").is_file()


def test_run_check_refuses_unknown_names_and_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []

    def fake_run(*_args, **kwargs):
        calls.append(kwargs.get("shell") is True)

        class Proc:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    refused = execute_check(tmp_path, {"pyok": 'python -c "print(1)"'}, "other")
    assert refused["ok"] is False and refused["error"]["code"] == "NOT_ALLOWED"
    assert calls == []
    allowed = execute_check(tmp_path, {"pyok": 'python -c "print(1)"'}, "pyok")
    assert allowed["ok"] is True
    assert calls == [False]


def test_fs_mcp_tool_name_keeps_git_diff() -> None:
    """CrewAI hashes tool names past 64 characters and drops the real name."""
    command, args = mcp_stdio()
    for tool in ("git_diff", "git_status", "run_check", "read_file"):
        raw = f"{command}_{'_'.join(args)}_{tool}"
        cleaned = "".join(char.lower() if char.isalnum() else "_" for char in raw)
        while "__" in cleaned:
            cleaned = cleaned.replace("__", "_")
        cleaned = cleaned.strip("_")
        assert len(cleaned) <= 64, cleaned
        assert cleaned.endswith(tool)


def test_summary_is_git_facts_and_notes_hold_agent_text() -> None:
    text = summary_from_files([FileChange(path="README.md", additions=1, deletions=0)])
    assert text == "README.md +1 -0"
    assert summary_from_files([]) == "No files changed."
    with pytest.raises(ValidationError):
        WorkerDraft(notes="")
    draft = WorkerDraft(notes="The sentence is on line 2.")
    assert "line 2" not in text
    assert draft.notes != text


def test_cursor_timeout_kills_the_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TREEFLEET_FAKE_SLEEP", "30")
    monkeypatch.delenv("TREEFLEET_FAKE_EDIT", raising=False)
    sink = EventSink(tmp_path / "runs")
    result = run_cursor(
        cli=FAKE,
        worktree=tmp_path,
        instruction="sleep",
        allowed=["README.md"],
        denied=[],
        model=None,
        timeout_s=1,
        sink=sink,
        run_id="ab12cd",
    )
    assert result.timed_out is True
    assert isinstance(result, CursorRunResult)
    leftovers = [
        proc
        for proc in psutil.process_iter(["cmdline"])
        if proc.info["cmdline"] and "s9_fakes.py" in " ".join(proc.info["cmdline"])
    ]
    assert leftovers == []
    events = [
        json.loads(line)
        for line in (tmp_path / "runs" / "ab12cd" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    cursor_steps = [
        event for event in events if event["type"] == "worker.step" and event["payload"]["step"] == "cursor"
    ]
    assert len(cursor_steps) == 1
    assert cursor_steps[0]["payload"]["timed_out"] is True
    assert cursor_steps[0]["payload"]["duration_s"] >= 0


def test_cursor_reverts_out_of_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "config", "user.email", "treefleet@local"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "config", "user.name", "TreeFleet"], cwd=repo, check=True, shell=False)
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, shell=False)
    monkeypatch.setenv("TREEFLEET_FAKE_EDIT", "outside")
    monkeypatch.setenv("TREEFLEET_FAKE_OUTSIDE", str(tmp_path / "escaped.txt"))
    monkeypatch.delenv("TREEFLEET_FAKE_SLEEP", raising=False)
    result = run_cursor(
        cli=FAKE,
        worktree=repo,
        instruction="edit",
        allowed=["README.md"],
        denied=[],
        model="composer-2.5-fast",
        timeout_s=15,
    )
    assert "sneak.txt" in result.reverted_paths
    assert not (repo / "sneak.txt").exists()
    assert (tmp_path / "escaped.txt").is_file()
    assert result.usage is not None and result.usage.input_tokens == 3
    assert result.exit_code == 0


def test_cursor_omits_model_flag_and_emits_output_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TREEFLEET_FAKE_SLEEP", raising=False)
    monkeypatch.delenv("TREEFLEET_FAKE_EDIT", raising=False)
    omitted = command_for(FAKE, tmp_path, None, "edit the readme")
    selected = command_for(FAKE, tmp_path, "composer-2.5-fast", "edit the readme")
    assert "--model" not in omitted
    assert selected[selected.index("--model") + 1] == "composer-2.5-fast"
    sink = EventSink(tmp_path / "runs")
    run_cursor(
        cli=FAKE,
        worktree=tmp_path,
        instruction="edit",
        allowed=["README.md"],
        denied=[],
        model=None,
        timeout_s=15,
        sink=sink,
        run_id="ab12cd",
    )
    lines = (tmp_path / "runs" / "ab12cd" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    output = [event for event in events if event["type"] == "output_line"]
    assert output
    assert all(event["source"] == "worker" and len(event["payload"]["line"]) <= 200 for event in output)
    cursor = [event for event in events if event["type"] == "worker.step" and event["payload"]["step"] == "cursor"]
    assert len(cursor) == 1
    assert cursor[0]["payload"]["timed_out"] is False


def test_windows_cursor_cmd_resolves_to_node(tmp_path: Path) -> None:
    version = tmp_path / "versions" / "2026.10.22-aaaaaaa"
    version.mkdir(parents=True)
    (version / "node.exe").write_bytes(b"")
    (version / "index.js").write_text("", encoding="utf-8")
    cmd = tmp_path / "cursor-agent.cmd"
    cmd.write_text("@echo off\n", encoding="utf-8")
    argv = command_for(cmd, tmp_path, "composer-2.5-fast", "edit the readme")
    assert argv[0].endswith("node.exe")
    assert argv[1].endswith("index.js")
    assert "--sandbox" in argv or sys.platform != "win32"
    assert argv[argv.index("--model") + 1] == "composer-2.5-fast"


def test_malformed_draft_retries_once() -> None:
    seen: list[str | None] = []

    def produce(error: str | None) -> str:
        seen.append(error)
        return "not json"

    with pytest.raises(ValidationError):
        draft_with_retry(produce)
    assert seen[0] is None
    assert seen[1] and "WorkerDraft" in seen[1]
    assert len(seen) == 2
    assert draft_with_retry(lambda error: '{"notes": "ok"}' if error or True else "") == WorkerDraft(notes="ok")
    with pytest.raises(ValidationError):
        WorkerDraft.model_validate({"summary": "old field"})


def _worker(env: dict[str, str], envelope: dict, worktree: Path) -> dict:
    code = (
        "import json, os\n"
        "from pathlib import Path\n"
        "from treefleet.contracts import WorkerTaskEnvelope\n"
        "from treefleet.worker.crew import run_task\n"
        "envelope = WorkerTaskEnvelope.model_validate_json(os.environ['ENVELOPE'])\n"
        "print(run_task(envelope, Path(os.environ['WORKTREE'])).model_dump_json())\n"
    )
    merged = os.environ.copy()
    merged.update(env)
    merged["ENVELOPE"] = json.dumps(envelope)
    merged["WORKTREE"] = str(worktree)
    merged["TREEFLEET_CURSOR_CLI"] = str(FAKE)
    proc = subprocess.run(
        [str(WORKER), "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        shell=False,
        env=merged,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr[-2000:] or proc.stdout[-2000:])
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if not lines or not lines[-1].startswith("{"):
        raise AssertionError((proc.stdout[-1500:] + "\n" + proc.stderr[-1500:]).strip())
    return json.loads(lines[-1])


def _envelope(
    worktree: Path,
    *,
    model: str | None = None,
    attempt: int = 1,
    feedback: str | None = None,
) -> dict:
    payload = {
        "run_id": "ab12cd",
        "attempt": attempt,
        "task": {
            "id": "alpha",
            "title": "Edit readme",
            "instructions": "Update README.md with a short note about the shim.",
            "allowed_paths": ["README.md"],
        },
        "model": "deepseek/deepseek-flash",
        "cursor_model": model,
        "checks": {"pyok": f"{WORKER} -c \"print('ok')\""},
    }
    if feedback:
        payload["revision_feedback"] = feedback
    return payload


def _git_worktree(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "config", "user.email", "treefleet@local"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "config", "user.name", "TreeFleet"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=repo, check=True, shell=False)
    (repo / "README.md").write_text("base\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, shell=False)
    prepare_repo(repo)
    run_id = uuid.uuid4().hex[:8]
    root = Path(r"C:\tf") if sys.platform == "win32" else Path("/tmp/tf")
    return repo, create_worktree(repo, run_id, "alpha", root), run_id


def test_worker_fake_cursor_commits_readme(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "config", "user.email", "treefleet@local"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "config", "user.name", "TreeFleet"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=repo, check=True, shell=False)
    (repo / "README.md").write_text("base\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, shell=False)
    prepare_repo(repo)
    run_id = uuid.uuid4().hex[:8]
    root = Path(r"C:\tf") if sys.platform == "win32" else Path("/tmp/tf")
    worktree = create_worktree(repo, run_id, "alpha", root)
    try:
        runs = tmp_path / "runs"
        payload = _worker(
            {"TREEFLEET_MOCK_LLM": "1", "TREEFLEET_FAKE_EDIT": "readme", "TREEFLEET_RUNS": str(runs)},
            _envelope(worktree),
            worktree,
        )
        assert payload["status"] == "done", payload.get("error")
        assert payload["head_sha"]
        change = payload["files_changed"][0]
        assert payload["summary"] == summary_from_files(
            [FileChange(path=change["path"], additions=change["additions"], deletions=change["deletions"])]
        )
        assert payload["notes"] == "Updated README.md."
        assert payload["notes"] not in payload["summary"]
        steps = [
            json.loads(line)["payload"]["step"]
            for line in (runs / "ab12cd" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if json.loads(line)["type"] == "worker.step"
        ]
        for name in ("start", "deepseek", "cursor", "check", "commit"):
            assert name in steps
        assert (
            subprocess.run(
                ["git", "cat-file", "-t", payload["head_sha"]], cwd=repo, capture_output=True, shell=False, check=False
            ).returncode
            == 0
        )
        probe = (
            "from treefleet.worker.crew import build_agent, tool_names\n"
            "from treefleet.contracts import WorkerTaskEnvelope\n"
            "import json, os\n"
            "from pathlib import Path\n"
            "e = WorkerTaskEnvelope.model_validate_json(os.environ['ENVELOPE'])\n"
            "print(','.join(tool_names(build_agent(e, Path(os.environ['WORKTREE'])))))\n"
        )
        names = subprocess.run(
            [str(WORKER), "-c", probe],
            capture_output=True,
            text=True,
            encoding="utf-8",
            shell=False,
            check=False,
            env={
                **os.environ,
                "ENVELOPE": json.dumps(_envelope(worktree)),
                "WORKTREE": str(worktree),
                "TREEFLEET_MOCK_LLM": "1",
                "TREEFLEET_CURSOR_CLI": str(FAKE),
            },
        )
        assert names.returncode == 0, names.stderr[-1000:]
        assert "cursor_code" in names.stdout
        assert "write" not in names.stdout.lower()
    finally:
        remove_worktree(repo, worktree)


def test_worker_outside_write_fails_through_the_guardrail(tmp_path: Path) -> None:
    repo, worktree, _run = _git_worktree(tmp_path)
    leaked = repo / "leaked.txt"
    guards = tmp_path / "guards.txt"
    try:
        payload = _worker(
            {
                "TREEFLEET_MOCK_LLM": "1",
                "TREEFLEET_FAKE_EDIT": "outside",
                "TREEFLEET_FAKE_OUTSIDE": str(leaked),
                "TREEFLEET_GUARD_COUNT": str(guards),
            },
            _envelope(worktree),
            worktree,
        )
        assert payload["status"] == "failed", payload.get("error")
        assert payload["commit_made"] is False
        assert "main checkout" in payload["error"]
        assert not (worktree / "sneak.txt").exists()
        assert leaked.is_file()
        assert int(guards.read_text(encoding="utf-8")) >= 2
    finally:
        remove_worktree(repo, worktree)


def test_worker_notes_without_git_diff_fail(tmp_path: Path) -> None:
    repo, worktree, _run = _git_worktree(tmp_path)
    calls = tmp_path / "calls.txt"
    try:
        payload = _worker(
            {"TREEFLEET_MOCK_LLM": "nodiff", "TREEFLEET_MOCK_COUNT": str(calls)},
            _envelope(worktree),
            worktree,
        )
        assert payload["status"] == "failed"
        assert payload["commit_made"] is False
        assert "git_diff" in payload["error"]
        assert calls.read_text(encoding="utf-8") == "2"
    finally:
        remove_worktree(repo, worktree)


def test_worker_bad_draft_retries_once_then_fails(tmp_path: Path) -> None:
    repo, worktree, _run = _git_worktree(tmp_path)
    calls = tmp_path / "calls.txt"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=worktree, capture_output=True, text=True, shell=False, check=True
    ).stdout.strip()
    try:
        payload = _worker(
            {"TREEFLEET_MOCK_LLM": "bad", "TREEFLEET_MOCK_COUNT": str(calls)},
            _envelope(worktree),
            worktree,
        )
        assert payload["status"] == "failed"
        assert payload["commit_made"] is False
        assert "WorkerDraft" in payload["error"]
        assert calls.read_text(encoding="utf-8") == "2"
        again = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=worktree, capture_output=True, text=True, shell=False, check=True
        ).stdout.strip()
        assert again == head
    finally:
        remove_worktree(repo, worktree)


def test_worker_revision_reuses_branch(tmp_path: Path) -> None:
    repo, worktree, _run = _git_worktree(tmp_path)
    try:
        first = _worker({"TREEFLEET_MOCK_LLM": "1", "TREEFLEET_FAKE_EDIT": "readme"}, _envelope(worktree), worktree)
        assert first["status"] == "done", first.get("error")
        second = _worker(
            {"TREEFLEET_MOCK_LLM": "1", "TREEFLEET_FAKE_EDIT": "readme", "TREEFLEET_FAKE_NOTE": "rev"},
            _envelope(worktree, attempt=2, feedback="Mention the revision."),
            worktree,
        )
        assert second["status"] == "done", second.get("error")
        assert second["attempt"] == 2
        assert second["branch"] == first["branch"]
        assert second["head_sha"] != first["head_sha"]
        parent = subprocess.run(
            ["git", "rev-parse", "HEAD^"], cwd=worktree, capture_output=True, text=True, shell=False, check=True
        ).stdout.strip()
        assert parent == first["head_sha"]
    finally:
        remove_worktree(repo, worktree)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_worker(worktree: Path, token: str, runs: Path, log: Path) -> tuple[subprocess.Popen[str], str]:
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    handle = log.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            str(WORKER),
            "-m",
            "treefleet.worker.main",
            "--port",
            str(port),
            "--worktree",
            str(worktree),
            "--token",
            token,
            "--run-id",
            "ab12cd",
            "--task-id",
            "alpha",
        ],
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        shell=False,
        env={
            **os.environ,
            "TREEFLEET_MOCK_LLM": "1",
            "TREEFLEET_FAKE_EDIT": "readme",
            "TREEFLEET_FAKE_SLEEP": "2",
            "TREEFLEET_CURSOR_CLI": str(FAKE),
            "TREEFLEET_RUNS": str(runs),
        },
    )
    handle.close()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(log.read_text(encoding="utf-8")[-2000:])
        try:
            response = httpx.get(url + "/.well-known/agent-card.json", timeout=1)
        except httpx.HTTPError:
            time.sleep(0.2)
            continue
        if response.status_code in {200, 401}:
            return proc, url
        time.sleep(0.2)
    raise AssertionError(log.read_text(encoding="utf-8")[-2000:])


def _worker_result(response: object) -> tuple[str, WorkerResult | None]:
    root = getattr(response, "root", None)
    result = getattr(root, "result", None)
    if isinstance(result, Task):
        if result.status.state == TaskState.rejected:
            return "rejected", None
        for artifact in result.artifacts or []:
            for part in artifact.parts:
                data = part.root
                if isinstance(data, DataPart):
                    return "result", WorkerResult.model_validate(data.data)
        return result.status.state.value, None
    error = getattr(root, "error", None)
    return f"error:{error}", None


async def test_a2a_card_client_and_busy_reject(tmp_path: Path) -> None:
    repo, worktree, _run = _git_worktree(tmp_path)
    token = "phase3-token"
    log = tmp_path / "worker.log"
    proc, url = _start_worker(worktree, token, tmp_path / "runs", log)
    try:
        async with httpx.AsyncClient(timeout=90) as http:
            denied = await http.get(url + "/.well-known/agent-card.json")
            assert denied.status_code == 401
            card = await http.get(url + "/.well-known/agent-card.json", headers={"Authorization": f"Bearer {token}"})
            assert card.status_code == 200
            body = card.json()
            assert body["capabilities"]["streaming"] is True
            assert body["name"] == "treefleet-worker-alpha"
            authed = httpx.AsyncClient(timeout=90, headers={"Authorization": f"Bearer {token}"})
            client = A2AClient(authed, url=url)

            def request(number: str) -> SendMessageRequest:
                message = Message(
                    message_id=str(uuid.uuid4()),
                    role=Role.user,
                    parts=[Part(root=DataPart(data=_envelope(worktree)))],
                )
                return SendMessageRequest(id=number, params=MessageSendParams(message=message))

            async def second() -> object:
                await asyncio.sleep(0.5)
                return await client.send_message(request("2"))

            first, later = await asyncio.gather(client.send_message(request("1")), second())
            await authed.aclose()
        kinds = [_worker_result(first), _worker_result(later)]
        assert any(kind == "rejected" for kind, _result in kinds), kinds
        done = [result for _kind, result in kinds if result is not None and result.status == "done"]
        assert len(done) == 1, kinds
        assert done[0].head_sha
        events = (tmp_path / "runs" / "ab12cd" / "events.jsonl").read_text(encoding="utf-8")
        assert '"type":"output_line"' in events or '"type": "output_line"' in events
    finally:
        kill_tree(proc.pid)
        proc.wait(timeout=10)
        remove_worktree(repo, worktree)
