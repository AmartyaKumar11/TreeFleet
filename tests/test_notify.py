"""needs_human on the board status endpoint survives refresh."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from treefleet.ui.lanes import run_needs_human, tasks_from_snapshot
from treefleet.ui.server import Approval, create_app

pytestmark = pytest.mark.phase8

ROOT = Path(__file__).resolve().parents[1]


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, shell=False)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init"], repo)
    _git(["config", "user.email", "tf@test"], repo)
    _git(["config", "user.name", "tf"], repo)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(["add", "README.md"], repo)
    _git(["commit", "-m", "base"], repo)
    return repo


def test_run_needs_human_for_awaiting_and_needs_lane() -> None:
    assert run_needs_human([], None, awaiting=True) is True
    assert run_needs_human([], None, awaiting=False) is False
    events = [
        {"type": "worker.exited", "task_id": "alpha", "payload": {"status": "failed"}},
    ]
    assert run_needs_human(events, None, awaiting=False) is True
    cards = tasks_from_snapshot(events, None, awaiting=False)
    assert any(card["lane"] == "needs" for card in cards)


def test_board_status_carries_needs_human(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    repo = _repo(tmp_path)
    runs = tmp_path / "runs"
    runs.mkdir()
    app = create_app(repo, root=ROOT, token="test-token", runs=runs, models=[])
    client = TestClient(app, base_url="http://127.0.0.1")
    headers = {"Authorization": "Bearer test-token"}
    empty = client.get("/api/board", headers=headers)
    assert empty.status_code == 200
    assert empty.json().get("needs_human") is False

    run_id = "deadbeef"
    run_dir = runs / run_id
    run_dir.mkdir()
    (run_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "seq": 1,
                "type": "worker.exited",
                "task_id": "alpha",
                "payload": {"status": "failed", "error": "boom"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    board = app.state.board
    board.approvals[run_id] = Approval()
    board.started[run_id] = 1.0
    snap = client.get("/api/board", headers=headers)
    assert snap.status_code == 200
    body = snap.json()
    assert body.get("needs_human") is True
    assert any(task.get("lane") == "needs" for task in body.get("tasks") or [])
