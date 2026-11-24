"""External UI API: base_ref, pr_base, repo gate, token file, pr_urls (offline)."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from treefleet.git.refs import RefNameError, prepare_base_ref, validate_ref_name
from treefleet.pr.config import PrSettings
from treefleet.pr.fake import FakeProvider
from treefleet.pr.loop import PollHooks
from treefleet.pr.stage import drive
from treefleet.ui.server import _pr_urls, create_app
from treefleet.ui.token_file import TokenFileError, load_or_create_token

pytestmark = pytest.mark.phase8

ROOT = Path(__file__).resolve().parents[1]


def _git(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@local"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token", "Host": "127.0.0.1"}


def test_ref_name_accepts_safe_branch_shapes() -> None:
    assert validate_ref_name("feature/x") == "feature/x"
    assert validate_ref_name("tf/ab12cd34/foo") == "tf/ab12cd34/foo"
    assert validate_ref_name("HEAD") == "HEAD"


@pytest.mark.parametrize(
    "bad",
    ["-x", "a..b", "a b", "a@{b", "feat\x00ure", "slash\\name", "  padded  ", ""],
)
def test_ref_name_rejects_unsafe(bad: str) -> None:
    with pytest.raises(RefNameError):
        validate_ref_name(bad)


def test_prepare_base_ref_head_no_fetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _git(repo)
    calls: list[list[str]] = []

    def fake_run_git(args: list[str], cwd: Path | None = None, timeout: float = 30):  # noqa: ARG001
        calls.append(list(args))
        from treefleet.git.worktrees import run_git as real

        return real(args, cwd=cwd, timeout=timeout)

    monkeypatch.setattr("treefleet.git.refs.run_git", fake_run_git)
    ref, sha = prepare_base_ref(repo, "HEAD")
    assert ref == "HEAD"
    assert len(sha) >= 7
    assert not any(args and args[0] == "fetch" for args in calls)


def test_prepare_base_ref_fetch_failure_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    _git(repo)

    class Proc:
        returncode = 1
        stdout = ""
        stderr = "could not resolve host"

    monkeypatch.setattr("treefleet.git.refs.run_git", lambda *a, **k: Proc())
    with pytest.raises(RuntimeError, match="could not fetch"):
        prepare_base_ref(repo, "feature/missing-branch")


def test_repo_mismatch_and_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    repo = tmp_path / "repo"
    other = tmp_path / "other"
    _git(repo)
    _git(other)
    app = create_app(repo, root=ROOT, token="test-token", models=[])
    client = TestClient(app, base_url="http://127.0.0.1")

    captured: dict[str, object] = {}

    def fake_launch(goal, worker_model, plan_approval, **kwargs):  # noqa: ANN001
        captured["goal"] = goal
        captured["kwargs"] = kwargs
        return "abcd1234"

    monkeypatch.setattr(app.state.board, "launch", fake_launch)

    bad = client.post(
        "/api/runs",
        headers=_headers(),
        json={
            "goal": "Add unit tests for the notifications helper module now.",
            "repo": str(other),
            "plan_approval": False,
        },
    )
    assert bad.status_code == 400
    assert bad.json()["error"] == f"this TreeFleet instance serves one repository: {repo.resolve()}"

    ok = client.post(
        "/api/runs",
        headers=_headers(),
        json={
            "goal": "Add unit tests for the notifications helper module now.",
            "repo": str(repo.resolve()),
            "plan_approval": False,
            "base_ref": "HEAD",
            "pr_base": "feature/pr-head",
        },
    )
    assert ok.status_code == 200
    assert ok.json()["run_id"] == "abcd1234"
    assert captured["kwargs"]["base_ref"] == "HEAD"
    assert captured["kwargs"]["pr_base"] == "feature/pr-head"


def test_invalid_base_ref_and_pr_base_400(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    client = TestClient(create_app(repo, root=ROOT, token="test-token", models=[]), base_url="http://127.0.0.1")
    for field, value in (("base_ref", "a..b"), ("pr_base", "-x")):
        body = {
            "goal": "Add unit tests for the notifications helper module now.",
            "plan_approval": False,
            field: value,
        }
        res = client.post("/api/runs", headers=_headers(), json=body)
        assert res.status_code == 400
        assert field in res.json()["error"]


def test_board_launch_builds_run_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    app = create_app(repo, root=ROOT, token="test-token", models=[], runs=tmp_path / "runs")
    board = app.state.board
    seen: dict[str, object] = {}

    async def fake_pipeline(request, settings, **kwargs):  # noqa: ANN001, ARG001
        seen["request"] = request
        from treefleet.orchestrator.pipeline import RunOutcome

        return RunOutcome(0, None, {}, "", "ok")

    monkeypatch.setattr("treefleet.orchestrator.pipeline.run_pipeline", fake_pipeline)
    monkeypatch.setattr("treefleet.ui.server._stub_fakes", lambda: None)
    monkeypatch.setattr("treefleet.checks.detect_checks", lambda _repo: [])
    monkeypatch.setattr(
        "treefleet.checks.select_checks",
        lambda *_a, **_k: MagicMock(checks={}, notes=[]),
    )
    monkeypatch.setattr(threading.Thread, "start", lambda self: self.run())

    board.launch(
        "Add unit tests for the notifications helper module now.",
        "router",
        False,
        base_ref="HEAD",
        pr_base="feature/original-head",
        base_sha="deadbeef",
    )
    req = seen["request"]
    assert req.base_ref == "HEAD"
    assert req.pr_base == "feature/original-head"


def test_pr_base_reaches_open_pr() -> None:
    fake = FakeProvider()
    fake.login = "ada"
    settings = PrSettings.model_validate(
        {
            "enabled": True,
            "repo": "octo/demo",
            "base": "should-not-win",
            "poll_seconds": 5,
            "trusted_commenters": ["ada"],
        }
    ).model_copy(update={"base": "feature/original-head"})
    hooks = PollHooks(
        revise=lambda *_a: "sha",
        resolve_conflict=lambda *_a: "sha",
        emit=lambda *_a: None,
        judge=lambda *_a: ("satisfied", "ok"),
    )
    opened, _outcome = drive(
        [{"id": "alpha", "title": "Alpha", "goal": "g", "scope": "s", "verdict": "approve"}],
        fake,
        hooks,
        settings,
        run_id="run",
        remote_url="https://github.com/octo/demo.git",
        push=lambda _task: "sha-alpha",
        login="ada",
        sleep=lambda _s: None,
        now=lambda: 0.0,
        deadline=0.0,
    )
    assert opened
    assert fake.opened[0].base == "feature/original-head"


def test_pr_settings_base_override_matches_pipeline() -> None:
    """Pipeline does settings.model_copy(update={pr: pr.model_copy(update={base})})."""
    original = PrSettings.model_validate({"enabled": True, "repo": "octo/demo", "base": "main"})
    effective = original.model_copy(update={"base": "feature/from-api"})
    assert effective.base == "feature/from-api"
    assert original.base == "main"


def test_pr_urls_deduped_in_task_order() -> None:
    tasks = [
        {"pr_url": "https://github.com/o/r/pull/1"},
        {"pr_url": ""},
        {"pr_url": "https://github.com/o/r/pull/2"},
        {"pr_url": "https://github.com/o/r/pull/1"},
    ]
    assert _pr_urls(tasks) == [
        "https://github.com/o/r/pull/1",
        "https://github.com/o/r/pull/2",
    ]


def test_board_snapshot_includes_pr_urls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    runs = tmp_path / "runs"
    run_id = "abcd1234"
    folder = runs / run_id
    folder.mkdir(parents=True)
    import json

    event = {
        "run_id": run_id,
        "seq": 1,
        "type": "pr.opened",
        "task_id": "alpha",
        "payload": {"number": 7, "url": "https://github.com/o/r/pull/7", "sha": "abc"},
    }
    (folder / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    app = create_app(repo, root=ROOT, token="test-token", runs=runs, models=[])
    board = app.state.board
    board.started[run_id] = 1.0
    board.goals[run_id] = "goal text here for the board"
    body = board.snapshot(run_id)
    assert body["pr_urls"] == ["https://github.com/o/r/pull/7"]
    assert any(t.get("pr_url") == "https://github.com/o/r/pull/7" for t in body["tasks"])


def test_token_file_create_reuse_mode(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "ui.token"
    with caplog.at_level(logging.DEBUG):
        first = load_or_create_token(path)
        second = load_or_create_token(path)
    assert first == second
    assert path.read_text(encoding="utf-8").strip() == first
    if os.name != "nt":
        mode = path.stat().st_mode & 0o777
        assert mode == 0o600
    # Token must not appear in captured logs from this helper (it logs nothing).
    assert first not in caplog.text


def test_token_file_atomic_excl(tmp_path: Path) -> None:
    path = tmp_path / "ui.token"
    token = load_or_create_token(path)
    # Simulate lost race: file exists → read path.
    assert load_or_create_token(path) == token
    if os.name != "nt":
        path.chmod(0o644)
        with pytest.raises(TokenFileError, match="0600"):
            load_or_create_token(path)


def test_cli_token_file_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    from treefleet import cli

    repo = tmp_path / "repo"
    _git(repo)
    token_path = tmp_path / "tok"
    seen: dict[str, object] = {}

    def fake_create_app(repo_arg, **kwargs):  # noqa: ANN001
        seen["repo"] = repo_arg
        seen["token"] = kwargs.get("token")
        return MagicMock()

    monkeypatch.setattr(uvicorn, "run", MagicMock())
    monkeypatch.setattr("treefleet.ui.server.create_app", fake_create_app)

    code = cli._ui(["--repo", str(repo), "--token-file", str(token_path), "--port", "8765"])
    assert code == 0
    assert token_path.is_file()
    token = token_path.read_text(encoding="utf-8").strip()
    assert seen["token"] == token
    assert token not in repr(uvicorn.run.mock_calls)
