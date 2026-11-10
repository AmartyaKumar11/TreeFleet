"""Credential preflight. Values are compared and never printed."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

import httpx
import pytest

from treefleet.config import load_settings
from treefleet.contracts import RunRequest
from treefleet.demo import env_differs, live_auth, preflight
from treefleet.evals.harness import load_env
from treefleet.orchestrator.agents import PlannerAuthError, plan_goal
from treefleet.orchestrator.pipeline import OfflineFakes, run_pipeline

pytestmark = pytest.mark.phase8

ROOT = Path(__file__).resolve().parents[1]
GOAL = "Add one sentence to README.md for each task."
SECRET = "sk-abcdefghijklmnopqrstuvwxyz"
OTHER = "sk-zzzzzzzzzzzzzzzzzzzzzzzzzz"
LINE = "DeepSeek authentication failed: check the process environment variable; it overrides .env"


def _git(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "config", "user.email", "treefleet@local"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "config", "user.name", "TreeFleet"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=repo, check=True, shell=False)
    (repo / "README.md").write_text("base\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "branch", "-M", "demo-base"], cwd=repo, check=True, shell=False)


def _no_secret(text: str) -> None:
    assert SECRET not in text
    assert OTHER not in text
    assert SECRET[:6] not in text
    assert SECRET[-6:] not in text


def test_load_env_names_the_source_and_keeps_the_process_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "process-typesafe")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    (tmp_path / ".env").write_text(
        "DEEPSEEK_API_KEY=file-deepseek\nTYPESAFE_API_KEY=file-typesafe\n",
        encoding="utf-8",
    )
    load_env(tmp_path)
    out = capsys.readouterr().out
    assert "DEEPSEEK_API_KEY from .env" in out
    assert "TYPESAFE_API_KEY from process" in out
    assert "file-deepseek" not in out
    assert "file-typesafe" not in out
    assert "process-typesafe" not in out
    assert os.environ["DEEPSEEK_API_KEY"] == "file-deepseek"
    assert os.environ["TYPESAFE_API_KEY"] == "process-typesafe"


def test_preflight_auth_is_red_without_the_key_and_differs_is_names_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "treefleet.demo.dotenv_values",
        lambda _root: {"DEEPSEEK_API_KEY": OTHER, "TYPESAFE_API_KEY": OTHER},
    )
    repo = tmp_path / "repo"
    _git(repo)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.deepseek.com":
            return httpx.Response(401, json={"request_id": "req-deep1", "error": {"message": f"key {SECRET} invalid"}})
        return httpx.Response(403, headers={"x-request-id": "req-jev01"}, json={"error": SECRET})

    def auth(env: Mapping[str, str]) -> list[tuple[bool, str]]:
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return live_auth(env, client=client)

    rows = preflight(
        repo,
        env={"DEEPSEEK_API_KEY": SECRET, "TYPESAFE_API_KEY": SECRET},
        login=lambda _cli: (True, ""),
        models=lambda _cli: (True, ""),
        auth=auth,
    )
    printed = "\n".join(text for _ok, text in rows)
    _no_secret(printed)
    assert any(
        not ok and text == "red    DEEPSEEK_API_KEY: process env differs from .env (process environment wins)"
        for ok, text in rows
    )
    assert any(
        not ok and text == "red    TYPESAFE_API_KEY: process env differs from .env (process environment wins)"
        for ok, text in rows
    )
    assert any(not ok and text == "red    DeepSeek authentication failed request req-deep1" for ok, text in rows)
    assert any(not ok and text == "red    Jev authentication failed request req-jev01" for ok, text in rows)
    assert env_differs({"DEEPSEEK_API_KEY": SECRET}, {"DEEPSEEK_API_KEY": SECRET}) == []


def test_preflight_accepts_a_200() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    rows = live_auth({"DEEPSEEK_API_KEY": SECRET, "TYPESAFE_API_KEY": SECRET}, client=client)
    printed = "\n".join(text for _ok, text in rows)
    _no_secret(printed)
    assert rows == [
        (True, "green  DeepSeek authentication accepted"),
        (True, "green  Jev authentication accepted"),
    ]


def test_request_id_that_contains_the_key_is_dropped() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"request_id": SECRET})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    rows = live_auth({"DEEPSEEK_API_KEY": SECRET}, client=client)
    assert rows == [(False, "red    DeepSeek authentication failed")]
    assert SECRET not in rows[0][1]


def _dying():
    from google.adk.models.base_llm import BaseLlm

    class Denied(Exception):
        status_code = 401

        def __str__(self) -> str:
            return f"Authentication Fails, api key {SECRET} is invalid"

    class Dying(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):  # noqa: ANN001
            del llm_request, stream
            raise Denied()
            yield  # pragma: no cover

    return Dying(model="deepseek/deepseek-flash")


async def test_planner_auth_failure_is_one_safe_line() -> None:
    with pytest.raises(PlannerAuthError) as caught:
        await plan_goal(_dying(), GOAL, "README.md\n", model_name="deepseek/deepseek-flash")
    assert str(caught.value) == LINE
    _no_secret(str(caught.value))


async def test_planner_auth_failure_ends_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    repo = tmp_path / "repo"
    _git(repo)
    outcome = await run_pipeline(
        RunRequest(goal=GOAL, repo=str(repo), plan_approval=False),
        load_settings(),
        fakes=OfflineFakes(planner=_dying(), cursor_cli=ROOT / "spikes" / "s9_fakes.py"),
        runs_root=tmp_path / "runs",
        worktree_root=tmp_path / "wt",
    )
    assert outcome.code == 1
    assert outcome.message == LINE
    out = capsys.readouterr().out
    assert LINE in out
    _no_secret(out)
    record = json.loads((tmp_path / "runs" / outcome.run_id / "run.json").read_text(encoding="utf-8"))
    assert record["stage"] == "failed"
