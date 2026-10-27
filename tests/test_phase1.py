"""Phase 1: config, events, model discovery, and the folder-first CLI."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from treefleet.cli import main
from treefleet.config import MissingSecretError, load_settings
from treefleet.contracts import RunEvent, from_wire
from treefleet.events import EventSink

pytestmark = pytest.mark.phase1

ROOT = Path(__file__).resolve().parents[1]
FAKE_CLI = ROOT / "spikes" / "s9_fakes.py"
GOAL = "Add a small documented config loader for the fleet."
OTHER_SECRET = "typesafe-secret-value"


def _toml(cli: Path) -> str:
    return f"""
[models]
fast = "deepseek/deepseek-flash"
strong = "deepseek/deepseek-v4-pro"
default_worker = "router"
default = ""

[models.router]
default_tier = "moderate"
simple = "gpt-5"
moderate = "gpt-5"
complex = "sonnet-4-thinking"

[cursor]
cli = {json.dumps(cli.as_posix())}
list_flag = "--list-models"
model_flag = "--model"
workspace_flag = "--workspace"
sandbox = "disabled"
flags = ["--print", "--output-format", "json", "--trust"]

[thresholds]
injection = 0.5
abusive = 0.5
on_topic = 0.3

[workers]
port_start = 9101

[paths]
worktree_windows = {json.dumps("C:\\wt")}
worktree_other = "/tmp/tf-wt"
orchestrator_venv = ".venv-orch"
worker_venv = ".venv-worker"

[pricing]
litellm_url = "https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json"
jev_usd_per_million_input = 0.042
jev_source_url = "https://api.typesafe.ai/v1/systemone"
"""


def _write(tmp: Path, text: str) -> Path:
    path = tmp / "treefleet.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", OTHER_SECRET)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)


def _event() -> RunEvent:
    return RunEvent(run_id="abc123", seq=0, source="api", type="api.run_started")


def test_config_rejects_unknown_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _secrets(monkeypatch)
    path = _write(tmp_path, _toml(FAKE_CLI) + "\nunknown = true\n")
    with pytest.raises(ValidationError):
        load_settings(path)


def test_config_missing_secret_names_the_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", OTHER_SECRET)
    path = _write(tmp_path, _toml(FAKE_CLI))
    with pytest.raises(MissingSecretError) as caught:
        load_settings(path)
    message = str(caught.value)
    assert "DEEPSEEK_API_KEY" in message
    assert OTHER_SECRET not in message


def test_config_loads_measured_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _secrets(monkeypatch)
    settings = load_settings(_write(tmp_path, _toml(FAKE_CLI)))
    assert settings.models.fast == "deepseek/deepseek-flash"
    assert settings.models.strong == "deepseek/deepseek-v4-pro"
    assert settings.models.default_worker == "router"
    assert settings.models.router.fallback() == "gpt-5"
    assert settings.models.router.model_for("trivial") == "gpt-5"
    assert settings.workers.port_start == 9101
    assert settings.cursor.model_flag == "--model"
    assert settings.cursor.sandbox == "disabled"
    assert "--print" in settings.cursor.flags
    expected = Path(r"C:\wt") if sys.platform == "win32" else Path("/tmp/tf-wt")
    assert Path(settings.worktree_root) == expected


@pytest.mark.asyncio
async def test_seq_is_gapless_under_concurrent_emitters(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "runs")

    async def send() -> None:
        await sink.emit(_event())

    await asyncio.gather(*[send() for _ in range(50)])
    lines = (tmp_path / "runs" / "abc123" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    events = [from_wire(json.loads(line), RunEvent) for line in lines]
    assert [event.seq for event in events] == list(range(50))


def test_sync_emit_is_gapless_across_threads(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "runs")
    threads = [threading.Thread(target=sink.emit_sync, args=(_event(),)) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    lines = (tmp_path / "runs" / "abc123" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    seqs = sorted(from_wire(json.loads(line), RunEvent).seq for line in lines)
    assert seqs == list(range(20))


@pytest.mark.asyncio
async def test_replay_returns_only_missed_events(tmp_path: Path) -> None:
    sink = EventSink(tmp_path / "runs")
    for _ in range(5):
        await sink.emit(_event())
    queue = await sink.subscribe("abc123", last_event_id=2)
    missed = [queue.get_nowait() for _ in range(queue.qsize())]
    assert [event.seq for event in missed] == [3, 4]


def test_cli_auto_is_router(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _secrets(monkeypatch)
    _write(tmp_path, _toml(FAKE_CLI))
    monkeypatch.chdir(tmp_path)
    assert main([GOAL, "--model", "auto", "--repo", str(tmp_path)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["worker_model"] == "router"
    assert payload["repo"] == str(tmp_path)


def test_unknown_model_is_rejected_before_a_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _secrets(monkeypatch)
    _write(tmp_path, _toml(FAKE_CLI))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as caught:
        main([GOAL, "--model", "not-a-real-model", "--repo", str(tmp_path)])
    message = str(caught.value)
    assert "not-a-real-model" in message
    assert "treefleet models" in message
    assert capsys.readouterr().out == ""
    assert not (tmp_path / "runs").exists()
