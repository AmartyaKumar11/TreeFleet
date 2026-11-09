"""The board shows the newest run, and approval records who granted it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from treefleet.sessions import RunStore, load_run
from treefleet.ui.server import create_app

pytestmark = pytest.mark.phase11

ROOT = Path(__file__).resolve().parents[1]


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")


def _git(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, shell=False)
    subprocess.run(["git", "config", "user.email", "t@local"], cwd=path, check=True, capture_output=True, shell=False)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True, capture_output=True, shell=False)
    (path / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True, shell=False)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True, shell=False)


def test_board_defaults_to_the_newest_run_and_history_can_select_the_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    runs = tmp_path / "runs"
    app = create_app(repo, root=ROOT, token="test-token", runs=runs, models=[])
    from treefleet.ui.server import Approval

    board = app.state.board
    board.started["aaaa1111"] = 10.0
    board.started["bbbb2222"] = 20.0
    board.approvals["aaaa1111"] = Approval()
    board.approvals["bbbb2222"] = Approval()
    (runs / "aaaa1111").mkdir(parents=True)
    (runs / "aaaa1111" / "events.jsonl").write_text(
        json.dumps({"type": "worker.spawned", "task_id": "alpha", "payload": {}}) + "\n",
        encoding="utf-8",
    )
    client = TestClient(app, base_url="http://127.0.0.1")
    headers = {"Authorization": "Bearer test-token"}
    latest = client.get("/api/board", headers=headers)
    assert latest.status_code == 200
    assert latest.json()["run_id"] == "bbbb2222"
    assert [item["id"] for item in latest.json()["runs"]] == ["bbbb2222", "aaaa1111"]
    older = client.get("/api/board", headers=headers, params={"run": "aaaa1111"})
    assert older.json()["run_id"] == "aaaa1111"
    assert older.json()["stage"] == "fleet"


def test_approve_header_sets_ui_or_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", runs=tmp_path / "runs", models=[])
    from treefleet.ui.server import Approval

    gate = Approval()
    app.state.board.approvals["abcd1234"] = gate
    client = TestClient(app, base_url="http://127.0.0.1")
    headers = {"Authorization": "Bearer test-token", "X-TreeFleet-Client": "ui"}
    response = client.post("/api/runs/abcd1234/approve", headers=headers)
    assert response.status_code == 200
    assert response.json()["source"] == "ui"
    assert gate.source == "ui"
    other = Approval()
    app.state.board.approvals["abcd1234"] = other
    plain = client.post("/api/runs/abcd1234/approve", headers={"Authorization": "Bearer test-token"})
    assert plain.json()["source"] == "http"
    assert other.source == "http"


def test_run_json_stores_plan_approval(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    store = RunStore.start(
        path,
        run_id="abcd1234",
        repo="D:/demo",
        base_sha="a" * 40,
        goal="Add unit tests for the notifier module.",
        routing_mode="router",
        pid=1,
        started=1.0,
        plan_approval=False,
    )
    loaded = load_run(path)
    assert loaded.plan_approval is False
    assert store.record.plan_approval is False
    bare = {
        "run_id": "abcd1234",
        "repo": "D:/demo",
        "base_sha": "a" * 40,
        "goal": "Add unit tests now.",
        "routing_mode": "router",
        "stage": "guard",
        "orchestrator_pid": 1,
        "orchestrator_started": 1.0,
        "updated_at": "2026-11-09T00:00:00+00:00",
    }
    path.write_text(json.dumps(bare), encoding="utf-8")
    assert load_run(path).plan_approval is True
