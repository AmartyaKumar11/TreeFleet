"""Phase 14.3: optional Jev, single .env load, verbose filter, try doctor-once."""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path

import pytest

from tests.test_phase4 import FAKE, GOAL, WT, _git, _plan_json, _port, _scripted
from treefleet.cli import configure_runtime, main
from treefleet.config import MissingSecretError, load_settings
from treefleet.contracts import RunRequest
from treefleet.onboarding import run_doctor, run_init
from treefleet.orchestrator.pipeline import OfflineFakes, RunOutcome, run_pipeline
from treefleet.sample.create import create_sample
from treefleet.try_cmd import run_try

pytestmark = pytest.mark.phase11


@pytest.fixture
def allow_long_wt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "treefleet.onboarding.worktree_budget",
        lambda root, *, limit=None: (True, 1, limit or 10_000),
    )


def test_settings_load_without_typesafe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-only")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    settings = load_settings()
    assert settings.deepseek_api_key == "deepseek-only"
    assert settings.typesafe_api_key == ""
    assert settings.jev_configured() is False


def test_empty_typesafe_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-only")
    monkeypatch.setenv("TYPESAFE_API_KEY", "   ")
    settings = load_settings()
    assert settings.jev_configured() is False


def test_missing_deepseek_still_names_only_that_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(MissingSecretError) as caught:
        load_settings()
    message = str(caught.value)
    assert "DEEPSEEK_API_KEY" in message
    assert "TYPESAFE_API_KEY" not in message


@pytest.mark.asyncio
async def test_run_without_typesafe_emits_skip_and_fallback_router(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    repo = tmp_path / "repo"
    _git(repo)
    llm, _calls = _scripted([_plan_json(["alpha"])])
    settings = load_settings()
    assert settings.jev_configured() is False
    outcome = await run_pipeline(
        RunRequest(goal=GOAL, repo=str(repo), plan_approval=False),
        settings,
        fakes=OfflineFakes(planner=llm, cursor_cli=FAKE, fake_sleep="0"),
        runs_root=tmp_path / "runs",
        worktree_root=WT,
        port_start=_port(),
    )
    assert outcome.code == 0, outcome.message
    assert outcome.report is not None
    assert outcome.report.router == "fallback"
    events = (tmp_path / "runs" / outcome.run_id / "events.jsonl").read_text(encoding="utf-8")
    assert "router.jev_skipped" in events
    assert "not_configured" in events
    report = json.loads((tmp_path / "runs" / outcome.run_id / "report.json").read_text(encoding="utf-8"))
    assert report["router"] == "fallback"


@pytest.mark.asyncio
async def test_run_with_typesafe_keeps_jev_router(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")
    repo = tmp_path / "repo"
    _git(repo)
    llm, _calls = _scripted([_plan_json(["alpha"])])

    class FakeJev:
        calls = 0
        input_tokens = 0

        def complexities(self, tasks: dict[str, str]) -> dict[str, str]:
            return {task_id: "trivial" for task_id in tasks}

        def probabilities(self, state: str, questions: dict[str, str]) -> dict[str, float]:
            return {name: (1.0 if name == "on_topic" else 0.0) for name in questions}

    settings = load_settings()
    assert settings.jev_configured() is True
    monkeypatch.setattr("treefleet.jev.client.JevClient", lambda *_a, **_k: FakeJev())
    outcome = await run_pipeline(
        RunRequest(goal=GOAL, repo=str(repo), plan_approval=False, use_jev=True),
        settings,
        fakes=OfflineFakes(planner=llm, cursor_cli=FAKE, fake_sleep="0", skip_jev=False),
        runs_root=tmp_path / "runs",
        worktree_root=WT,
        port_start=_port(),
    )
    assert outcome.code == 0, outcome.message
    assert outcome.report is not None
    assert outcome.report.router == "jev"
    events = (tmp_path / "runs" / outcome.run_id / "events.jsonl").read_text(encoding="utf-8")
    assert "router.jev_skipped" not in events


def _point_venvs(folder: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    orch = (root / ".venv-orch").resolve().as_posix()
    worker = (root / ".venv-worker").resolve().as_posix()
    text = (folder / "treefleet.toml").read_text(encoding="utf-8")
    if "orchestrator_venv" not in text:
        text = text.replace(
            "[paths]\n",
            f"[paths]\norchestrator_venv = {orch!r}\nworker_venv = {worker!r}\n",
            1,
        )
    else:
        # Replace whole assignment lines so absolute paths are not doubled.
        lines: list[str] = []
        for line in text.splitlines():
            if line.strip().startswith("orchestrator_venv"):
                lines.append(f"orchestrator_venv = {orch!r}")
            elif line.strip().startswith("worker_venv"):
                lines.append(f"worker_venv = {worker!r}")
            else:
                lines.append(line)
        text = "\n".join(lines) + "\n"
    (folder / "treefleet.toml").write_text(text, encoding="utf-8")


def test_doctor_typesafe_unset_is_yellow(tmp_path: Path, allow_long_wt: None) -> None:
    wt = tmp_path / "wt"
    wt.mkdir()
    folder = tmp_path / "proj"
    assert run_init(folder, yes=True, worktree_root=str(wt), enable_pr=False)[0] == 0
    _point_venvs(folder)
    code, rows = run_doctor(
        cwd=folder,
        env={"DEEPSEEK_API_KEY": "present"},
        login=lambda _cli: (True, ""),
        models=lambda _cli: (True, ""),
        interpreter=lambda _repo, _py: [
            (True, "green  worker interpreter imports crewai (fake)"),
            (True, "green  check interpreter imports pytest (fake)"),
        ],
    )
    assert code == 0
    text = "\n".join(line for _ok, line in rows)
    assert "yellow TYPESAFE_API_KEY is unset" in text
    assert "built-in fallback" in text
    assert not any(not ok and "TYPESAFE_API_KEY" in line for ok, line in rows)


def test_init_marks_typesafe_optional(tmp_path: Path, allow_long_wt: None) -> None:
    wt = tmp_path / "wt"
    code, notes = run_init(tmp_path / "proj", yes=True, worktree_root=str(wt), enable_pr=False)
    assert code == 0
    example = (tmp_path / "proj" / ".env.example").read_text(encoding="utf-8")
    assert "Optional. Jev" in example
    assert any("optional" in note.lower() and "TYPESAFE" in note for note in notes)


def test_cli_loads_dotenv_once_process_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], allow_long_wt: None
) -> None:
    wt = tmp_path / "wt"
    wt.mkdir()
    folder = tmp_path / "proj"
    assert run_init(folder, yes=True, worktree_root=str(wt), enable_pr=False)[0] == 0
    _point_venvs(folder)
    (folder / ".env").write_text(
        "DEEPSEEK_API_KEY=file-deepseek\nTYPESAFE_API_KEY=file-typesafe\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "process-deepseek")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("TREEFLEET_CONFIG", str(folder / "treefleet.toml"))
    monkeypatch.chdir(folder)
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "DEEPSEEK_API_KEY from process" in out
    assert "TYPESAFE_API_KEY from .env" in out
    assert os.environ["DEEPSEEK_API_KEY"] == "process-deepseek"
    assert os.environ["TYPESAFE_API_KEY"] == "file-typesafe"
    assert out.count("DEEPSEEK_API_KEY from process") == 1
    assert out.count("TYPESAFE_API_KEY from .env") == 1


def test_verbose_flag_controls_experimental_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TREEFLEET_VERBOSE", raising=False)
    configure_runtime(verbose=False)
    assert any(entry[0] == "ignore" and "EXPERIMENTAL" in str(entry[1]) for entry in warnings.filters)
    configure_runtime(verbose=True)
    assert any(entry[0] == "default" and "EXPERIMENTAL" in str(entry[1]) for entry in warnings.filters)


def test_try_prints_doctor_lines_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "present")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    dest = tmp_path / "sample"
    create_sample(dest)
    marker = "green  unique-doctor-line-for-once-test"

    def doctor(**_kwargs):  # type: ignore[no-untyped-def]
        print(marker, flush=True)
        return 0, [(True, marker)]

    def pipeline(*_a, **_k):  # type: ignore[no-untyped-def]
        return RunOutcome(0, None, {}, "abcd1234")

    code, _notes = run_try(
        dest=dest,
        yes=True,
        doctor=doctor,
        pipeline=pipeline,
        settings=load_settings(),
    )
    assert code == 0
    out = capsys.readouterr().out
    assert out.count(marker) == 1
