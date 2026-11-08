"""Concurrent event appends stay one JSON object per line, and readers skip a torn line."""

from __future__ import annotations

import json
import multiprocessing
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from treefleet.cli import main
from treefleet.events import append_lines, read_events
from treefleet.reactions import explain_events
from treefleet.ui.server import create_app

pytestmark = pytest.mark.phase8

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "a2f1be0b-events.jsonl"


def _append_batch(spec: tuple[str, int]) -> None:
    path, worker = spec
    target = Path(path)
    for index in range(2000):
        append_lines(target, [json.dumps({"w": worker, "i": index}, separators=(",", ":"))])


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


def _run_json() -> str:
    return json.dumps(
        {
            "run_id": "abcd1234",
            "repo": "D:/demo",
            "base_sha": "abc",
            "goal": "Add unit tests for the demo modules.",
            "routing_mode": "router",
            "stage": "done",
            "orchestrator_pid": 1,
            "orchestrator_started": 1.0,
            "updated_at": "2026-11-08T00:00:00+00:00",
            "tasks": [],
        }
    )


def test_four_processes_append_without_torn_lines(tmp_path: Path) -> None:
    target = tmp_path / "events.jsonl"
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(4) as pool:
        pool.map(_append_batch, [(str(target), worker) for worker in range(4)])
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 8000
    seen: set[tuple[int, int]] = set()
    for line in lines:
        item = json.loads(line)
        seen.add((item["w"], item["i"]))
    assert seen == {(worker, index) for worker in range(4) for index in range(2000)}


def test_readers_report_the_corrupt_live_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rows, skipped = read_events(FIXTURE)
    assert skipped == 1
    assert len(rows) == 466
    text = explain_events(FIXTURE.read_text(encoding="utf-8").splitlines())
    assert text.startswith("skipped 1 corrupt event lines")

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    run = tmp_path / "runs" / "abcd1234"
    run.mkdir(parents=True)
    (run / "run.json").write_text(_run_json(), encoding="utf-8")
    (run / "events.jsonl").write_text('{"type":"phase","payload":{"name":"fleet"}}\nl":""}}\n', encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    assert main(["sessions", "show", "abcd1234"]) == 0
    assert "skipped 1 corrupt event lines" in capsys.readouterr().out

    create_app(repo, root=ROOT, token="test-token", replay=run / "events.jsonl", runs=tmp_path / "runs", models=[])
    assert "skipped 1 corrupt event lines" in capsys.readouterr().out

    live = create_app(repo, root=ROOT, token="test-token", runs=tmp_path / "runs", models=[])
    live.state.board.finished.add("abcd1234")
    client = TestClient(live, base_url="http://127.0.0.1")
    client.get("/api/runs/abcd1234/events", headers={"Authorization": "Bearer test-token"})
    assert "skipped 1 corrupt event lines" in capsys.readouterr().out
