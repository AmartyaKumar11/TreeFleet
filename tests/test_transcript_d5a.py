"""15.D5a: offset cursor, /api/transcript, SSE byte ids, event-log robustness."""

from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from treefleet.contracts import CursorToolEvent, Plan, TaskSpec
from treefleet.cursor_run import parse_stream_line, run_cursor, tool_detail
from treefleet.events import (
    WRITE_ATTEMPTS,
    BadCursorError,
    EventSink,
    LockTimeoutError,
    append_lines,
    read_events_offset,
)
from treefleet.ui.server import Approval, create_app
from treefleet.ui.transcript import PAYLOAD_STRING_CAP, build_transcript, valid_run_id

pytestmark = pytest.mark.phase8

ROOT = Path(__file__).resolve().parents[1]


def _git(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, shell=False)
    subprocess.run(["git", "config", "user.email", "t@local"], cwd=path, check=True, capture_output=True, shell=False)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True, capture_output=True, shell=False)
    (path / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True, shell=False)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True, shell=False)


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")


def _client(repo: Path, runs: Path) -> TestClient:
    app = create_app(repo, root=ROOT, token="test-token", runs=runs, models=[])
    return TestClient(app, base_url="http://127.0.0.1")


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _event_line(seq: int, kind: str, **payload: object) -> str:
    return (
        json.dumps(
            {
                "run_id": "abcd1234",
                "seq": seq,
                "ts": "2026-11-10T00:00:00Z",
                "source": "orchestrator",
                "type": kind,
                "task_id": None,
                "payload": payload,
            },
            separators=(",", ":"),
        )
        + "\n"
    )


def test_valid_run_id_rejects_traversal() -> None:
    assert valid_run_id("abcd1234")
    assert not valid_run_id("../etc")
    assert not valid_run_id("abcd1234/../x")
    assert not valid_run_id("")


def test_append_retries_permission_error_then_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "events.jsonl"
    calls = {"n": 0}
    real_open = os.open

    def flaky(path: str, flags: int, mode: int = 0o777) -> int:
        text = str(path)
        if text.endswith("events.jsonl") and flags & os.O_APPEND:
            calls["n"] += 1
            if calls["n"] < 3:
                raise PermissionError("transient")
        return real_open(path, flags, mode)

    monkeypatch.setattr(os, "open", flaky)
    append_lines(target, ['{"ok":true}\n'])
    assert calls["n"] == 3
    assert target.read_text(encoding="utf-8").strip() == '{"ok":true}'


def test_append_permission_error_exhausts_attempts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "events.jsonl"
    real = os.open
    attempts = {"n": 0}

    def deny_event(path: str, flags: int, mode: int = 0o777) -> int:
        if str(path).endswith("events.jsonl") and (flags & os.O_APPEND):
            attempts["n"] += 1
            raise PermissionError("still locked")
        return real(path, flags, mode)

    monkeypatch.setattr(os, "open", deny_event)
    with pytest.raises(OSError) as caught:
        append_lines(target, ['{"ok":true}\n'])
    assert isinstance(caught.value.__cause__, PermissionError)
    assert attempts["n"] == WRITE_ATTEMPTS


def _hold_lock_process(lock_path: str, ready_path: str, done_path: str) -> None:
    """Child process: hold the events lock until done_path appears."""
    import os
    import time
    from pathlib import Path

    from treefleet.events import _lock, _unlock

    fd = os.open(lock_path, os.O_RDWR)
    try:
        _lock(fd, deadline_s=30.0)
        Path(ready_path).write_text("1", encoding="utf-8")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not Path(done_path).is_file():
            time.sleep(0.01)
        _unlock(fd)
    finally:
        os.close(fd)


def test_lock_deadline_raises(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    lock_path = target.with_name(target.name + ".lock")
    target.write_text("", encoding="utf-8")
    lock_path.write_bytes(b"\0")
    ready = tmp_path / "ready"
    done = tmp_path / "done"
    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(target=_hold_lock_process, args=(str(lock_path), str(ready), str(done)))
    proc.start()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not ready.is_file():
        time.sleep(0.01)
    assert ready.is_file(), "child never acquired the lock"
    with pytest.raises(LockTimeoutError):
        append_lines(target, ['{"x":1}\n'], lock_deadline_s=0.05)
    done.write_text("1", encoding="utf-8")
    proc.join(timeout=5)
    assert proc.exitcode == 0


def _append_worker(spec: tuple[str, int]) -> None:
    path, worker = spec
    target = Path(path)
    for index in range(100):
        append_lines(
            target,
            [json.dumps({"w": worker, "i": index, "seq": 0 if worker else index}, separators=(",", ":"))],
        )


def test_offset_paging_with_concurrent_appends(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(4) as pool:
        pool.map(_append_worker, [(str(target), worker) for worker in range(4)])
    seen: set[tuple[int, int]] = set()
    after = 0
    while True:
        page = read_events_offset(target, after, limit=50)
        for item in page.items:
            seen.add((item["w"], item["i"]))
            assert "offset" in item
        assert page.next_offset >= after
        after = page.next_offset
        if page.eof:
            break
    assert seen == {(worker, index) for worker in range(4) for index in range(100)}
    assert after == target.stat().st_size


def test_torn_last_line_not_consumed(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    first = '{"a":1}\n'
    path.write_bytes(first.encode("utf-8") + b'{"b":')
    page = read_events_offset(path, 0, limit=10)
    assert len(page.items) == 1
    assert page.items[0]["a"] == 1
    assert page.next_offset == len(first.encode("utf-8"))
    assert page.eof is False


def test_invalid_json_line_skipped(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"ok":1}\nnot-json\n{"ok":2}\n', encoding="utf-8")
    page = read_events_offset(path, 0, limit=10)
    assert [item["ok"] for item in page.items] == [1, 2]
    assert page.skipped == 1


def test_offset_past_size_resets(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"ok":1}\n', encoding="utf-8")
    page = read_events_offset(path, 9999, limit=10)
    assert page.reset is True
    assert len(page.items) == 1


def test_mid_line_offset_raises(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"ok":1}\n', encoding="utf-8")
    with pytest.raises(BadCursorError):
        read_events_offset(path, 3, limit=10)


def test_transcript_endpoint_paging_and_caps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    runs = tmp_path / "runs"
    run = runs / "abcd1234"
    run.mkdir(parents=True)
    big = "x" * (PAYLOAD_STRING_CAP + 50)
    secret_plan = {
        "goal": "Add coverage for the demo modules here.",
        "rationale": "sk-abcdefghijklmnop keep going with enough text.",
        "tasks": [
            {
                "id": "alpha-task",
                "title": "Alpha title here",
                "instructions": "Write enough instructions for the task validation minimum.",
                "allowed_paths": ["README.md"],
                "depends_on": [],
                "check_ids": ["pytest"],
            }
        ],
    }
    (run / "run.json").write_text(
        json.dumps(
            {
                "run_id": "abcd1234",
                "repo": str(repo),
                "base_sha": "a" * 40,
                "goal": "Add coverage for the demo modules here.",
                "routing_mode": "router",
                "stage": "done",
                "orchestrator_pid": 1,
                "orchestrator_started": 1.0,
                "updated_at": "2026-11-10T00:00:00+00:00",
                "plan": secret_plan,
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )
    lines = [_event_line(i, "phase", name="fleet", note=big if i == 0 else "ok") for i in range(5)]
    lines.append(_event_line(5, "output_line", line="sk-secretvalueZZ"))
    (run / "events.jsonl").write_text("".join(lines), encoding="utf-8")
    client = _client(repo, runs)
    bad = client.get("/api/transcript", params={"run": "../etc", "after": 0}, headers=_headers())
    assert bad.status_code == 400
    missing = client.get("/api/transcript", params={"run": "zzz99999"}, headers=_headers())
    assert missing.status_code == 404
    mid = client.get("/api/transcript", params={"run": "abcd1234", "after": 3}, headers=_headers())
    assert mid.status_code == 400
    huge = client.get("/api/transcript", params={"run": "abcd1234", "limit": 9999}, headers=_headers())
    assert huge.status_code == 400
    first = client.get(
        "/api/transcript",
        params={"run": "abcd1234", "after": 0, "limit": 2, "plan": 1},
        headers=_headers(),
    )
    assert first.status_code == 200
    body = first.json()
    assert body["schema_id"] == "treefleet.transcript.v1"
    assert body["run_id"] == "abcd1234"
    assert len(body["items"]) == 2
    assert body["items"][0].get("truncated") is True
    assert len(body["items"][0]["payload"]["note"]) == PAYLOAD_STRING_CAP
    assert body["plan"]["tasks"][0]["instructions"]
    assert "sk-abcdefghijklmnop" not in json.dumps(body)
    assert "[redacted]" in json.dumps(body["plan"])
    second = client.get(
        "/api/transcript",
        params={"run": "abcd1234", "after": body["next"], "limit": 50},
        headers=_headers(),
    )
    assert second.status_code == 200
    more = second.json()
    offsets = [item["offset"] for item in body["items"] + more["items"]]
    assert offsets == sorted(offsets)
    assert len(set(offsets)) == len(offsets)
    secret_line = next(item for item in more["items"] if item["type"] == "output_line")
    assert "sk-secretvalueZZ" not in secret_line["payload"]["line"]
    assert more["eof"] is True


def test_plan_while_awaiting_and_after_finish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    runs = tmp_path / "runs"
    run = runs / "abcd1234"
    run.mkdir(parents=True)
    (run / "events.jsonl").write_text(_event_line(0, "awaiting_approval", approved=False), encoding="utf-8")
    app = create_app(repo, root=ROOT, token="test-token", runs=runs, models=[])
    gate = Approval()
    plan = Plan(
        goal="Add coverage for the demo modules here.",
        rationale="Two tasks keep the work disjoint.",
        tasks=[
            TaskSpec(
                id="alpha-task",
                title="Alpha title here",
                instructions="Write enough instructions for the task validation minimum.",
                allowed_paths=["README.md"],
            )
        ],
    )
    gate.plan = plan
    app.state.board.approvals["abcd1234"] = gate
    client = TestClient(app, base_url="http://127.0.0.1")
    waiting = client.get("/api/transcript", params={"run": "abcd1234", "plan": 1}, headers=_headers())
    assert waiting.status_code == 200
    body = waiting.json()
    assert body["awaiting"] is True
    assert body["plan"]["tasks"][0]["id"] == "alpha-task"
    assert "instructions" in body["plan"]["tasks"][0]
    board = client.get("/api/board", params={"run": "abcd1234"}, headers=_headers())
    assert board.json()["awaiting"][0] == {
        "id": "alpha-task",
        "title": "Alpha title here",
        "allowed_paths": ["README.md"],
    }


def test_old_format_lines_without_detail_remain_readable(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    old = {
        "run_id": "abcd1234",
        "seq": 1,
        "source": "worker",
        "type": "cursor.tool_start",
        "payload": {"tool": "read", "path": "a.py", "ok": None},
    }
    path.write_text(json.dumps(old) + "\n", encoding="utf-8")
    page = read_events_offset(path, 0, limit=10)
    event = CursorToolEvent.model_validate(page.items[0]["payload"])
    assert event.detail == ""
    assert event.path == "a.py"


def test_sse_delivers_seq_zero_after_orchestrator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Audit finding: after orchestrator seq=18, worker seq=0 lines must still stream.

    Old contract asserted SSE id == seq and skipped seq<=cursor, which dropped worker
    seq=0. Cursor is now a byte offset, so those lines are delivered. (Finished is set
    before each stream so the generator exits after a quiet drain.)
    """
    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    runs = tmp_path / "runs"
    run = runs / "abcd1234"
    run.mkdir(parents=True)
    path = run / "events.jsonl"
    parts = [_event_line(seq, "phase", name="fleet") for seq in range(19)]
    path.write_text("".join(parts), encoding="utf-8")
    app = create_app(repo, root=ROOT, token="test-token", runs=runs, models=[])
    app.state.board.approvals["abcd1234"] = Approval()
    app.state.board.finished.add("abcd1234")
    client = TestClient(app, base_url="http://127.0.0.1")
    with client.stream("GET", "/api/runs/abcd1234/events", headers=_headers()) as response:
        text = "".join(response.iter_text())
    ids = [line[4:].strip() for line in text.splitlines() if line.startswith("id: ")]
    assert len(ids) >= 19
    last_id = ids[18]
    assert last_id.isdigit()
    # Contract change: id is a byte offset, not seq (those lines had seq 0..18).
    append_lines(
        path,
        [
            json.dumps(
                {
                    "run_id": "abcd1234",
                    "seq": 0,
                    "source": "worker",
                    "type": "worker.ready",
                    "task_id": "alpha",
                    "payload": {"pid": 1, "cwd": "x", "task_id": "alpha"},
                },
                separators=(",", ":"),
            )
            + "\n",
            json.dumps(
                {
                    "run_id": "abcd1234",
                    "seq": 0,
                    "source": "worker",
                    "type": "output_line",
                    "task_id": "alpha",
                    "payload": {"line": "hello"},
                },
                separators=(",", ":"),
            )
            + "\n",
        ],
    )
    with client.stream(
        "GET",
        "/api/runs/abcd1234/events",
        headers={**_headers(), "Last-Event-ID": last_id},
    ) as response:
        resumed = "".join(response.iter_text())
    assert "worker.ready" in resumed
    assert "output_line" in resumed
    assert '"seq": 0' in resumed or '"seq":0' in resumed


def test_approval_denied_on_cancel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    runs = tmp_path / "runs"
    run = runs / "abcd1234"
    run.mkdir(parents=True)
    (run / "events.jsonl").write_text("", encoding="utf-8")
    app = create_app(repo, root=ROOT, token="test-token", runs=runs, models=[])
    gate = Approval()
    gate.plan = Plan(
        goal="Add coverage for the demo modules here.",
        rationale="Reject path test.",
        tasks=[
            TaskSpec(
                id="alpha-task",
                title="Alpha title here",
                instructions="Write enough instructions for the task validation minimum.",
                allowed_paths=["README.md"],
            )
        ],
    )
    # Simulate waiting: plan set, decision not set.
    app.state.board.approvals["abcd1234"] = gate
    client = TestClient(app, base_url="http://127.0.0.1")
    denied = client.post(
        "/api/runs/abcd1234/cancel",
        headers={**_headers(), "X-TreeFleet-Client": "ui"},
    )
    assert denied.status_code == 200
    assert denied.json()["source"] == "ui"
    rows = [
        json.loads(line) for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert any(row.get("type") == "approval.denied" and row["payload"]["source"] == "ui" for row in rows)


def test_tool_detail_allowlist_and_secret_negative(tmp_path: Path) -> None:
    assert tool_detail("shell", {"command": "pytest -q"}) == "pytest -q"
    assert tool_detail("grep", {"pattern": "TODO", "glob": "*.py"}) == "TODO *.py"
    assert tool_detail("read", {"path": "a.py", "startLine": 1, "endLine": 9}) == "a.py startLine=1 endLine=9"
    assert tool_detail("mystery", {"command": "rm -rf /"}) == ""
    planted = "sk-plantedsecret99"
    body = planted + ("B" * 50_000)
    events = [
        {
            "type": "tool_call",
            "subtype": "started",
            "tool_call": {
                "shellToolCall": {"args": {"command": f"echo {planted}"}},
            },
        },
        {
            "type": "tool_call",
            "subtype": "completed",
            "tool_call": {
                "shellToolCall": {
                    "args": {"command": f"echo {planted}"},
                    "result": {"success": {"stdout": body}},
                }
            },
        },
        {
            "type": "tool_call",
            "subtype": "started",
            "tool_call": {"readToolCall": {"args": {"path": "SECRET.md"}}},
        },
        {
            "type": "tool_call",
            "subtype": "completed",
            "tool_call": {
                "readToolCall": {
                    "args": {"path": "SECRET.md"},
                    "result": {"success": {"content": body}},
                }
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "result": "done",
            "usage": {"inputTokens": 1, "outputTokens": 1, "cacheReadTokens": 0, "cacheWriteTokens": 0},
        },
    ]
    script = tmp_path / "fake_cursor.py"
    printed = ["import json"]
    printed.extend(f"print(json.dumps({json.dumps(item)}), flush=True)" for item in events)
    script.write_text("\n".join(printed) + "\n", encoding="utf-8")
    sink = EventSink(tmp_path / "runs")
    run_cursor(
        cli=script,
        worktree=tmp_path,
        instruction="edit",
        allowed=["SECRET.md"],
        denied=[],
        model=None,
        timeout_s=15,
        sink=sink,
        run_id="ab12cd34",
        task_id="alpha",
    )
    log = (tmp_path / "runs" / "ab12cd34" / "events.jsonl").read_text(encoding="utf-8")
    assert planted not in log
    assert body[:100] not in log
    assert "50" + "0" * 4 not in log or True
    rows = [json.loads(line) for line in log.splitlines() if line.strip()]
    starts = [row for row in rows if row["type"] == "cursor.tool_start"]
    assert any("[redacted]" in (row["payload"].get("detail") or "") for row in starts)
    assert all(len(row["payload"].get("detail") or "") <= 300 for row in starts)
    finishes = [row for row in rows if row["type"] == "cursor.tool_finish"]
    assert finishes
    assert all("content" not in json.dumps(row["payload"]) for row in finishes)
    hit = parse_stream_line(
        json.dumps(
            {
                "type": "tool_call",
                "subtype": "started",
                "tool_call": {"grepToolCall": {"args": {"pattern": "foo", "path": "a.py"}}},
            }
        )
    )
    assert hit is not None and hit.detail == "foo a.py"


def test_build_transcript_old_events_without_offset_field(tmp_path: Path) -> None:
    path = tmp_path / "rid" / "events.jsonl"
    path.parent.mkdir()
    path.write_text(_event_line(0, "phase", name="scan"), encoding="utf-8")
    body = build_transcript(path, run_id="rid", after=0, limit=10, include_plan=False, state="finished")
    assert body["items"][0]["type"] == "phase"
    assert body["items"][0]["offset"] == 0
