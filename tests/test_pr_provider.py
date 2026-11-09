"""Provider and [pr] config. No network."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from treefleet.config import ConfigError, load_settings
from treefleet.pr.fake import FakeProvider
from treefleet.pr.gh import GhError, GhProvider

pytestmark = pytest.mark.phase11

ROOT = Path(__file__).resolve().parents[1]


def _secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")


def test_missing_pr_table_stays_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _secrets(monkeypatch)
    settings = load_settings(ROOT / "treefleet.toml")
    assert settings.pr.enabled is False
    assert settings.pr.allow_external_hooks is False
    assert settings.pr.poll_seconds == 15
    assert settings.pr.auto_merge is False


def test_poll_seconds_below_five_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _secrets(monkeypatch)
    text = (ROOT / "treefleet.toml").read_text(encoding="utf-8")
    path = tmp_path / "treefleet.toml"
    path.write_text(text + "\n[pr]\nenabled = false\npoll_seconds = 4\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="poll_seconds"):
        load_settings(path)


def test_fake_provider_is_scriptable() -> None:
    fake = FakeProvider()
    opened = fake.open_pr("tf/run/alpha", "demo-base", "Alpha", "Goal: tests")
    fake.set_state(opened.number, check_runs=(), head_sha="abc")
    assert fake.get_pr(opened.number).head_sha == "abc"
    fake.add_comment(opened.number, "ada", "looks good")
    assert fake.list_comments(opened.number, 0)[0].author == "ada"
    assert fake.list_comments(opened.number, 99) == []
    fake.merge(opened.number)
    assert fake.merged == [opened.number]
    assert fake.get_pr(opened.number).state == "merged"


def test_gh_retries_transient_errors_and_always_passes_repo() -> None:
    calls: list[list[str]] = []

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if len(calls) < 3:
            return subprocess.CompletedProcess(args, 1, "", "HTTP 503 rate limit")
        return subprocess.CompletedProcess(args, 0, "https://github.com/octo/demo/pull/7\n", "")

    views = {"n": 0}

    def view(args: list[str]) -> subprocess.CompletedProcess[str]:
        if "view" in args:
            views["n"] += 1
            body = '{"state":"OPEN","mergeable":"MERGEABLE","statusCheckRollup":[],"headRefOid":"abc","url":"https://github.com/octo/demo/pull/7"}'
            return subprocess.CompletedProcess(args, 0, body, "")
        return runner(args)

    provider = GhProvider("octo/demo", runner=view, sleep=lambda _seconds: None)
    opened = provider.open_pr("tf/run/alpha", "demo-base", "Alpha", "body")
    assert opened.number == 7
    assert opened.head_sha == "abc"
    assert all(args[0] == "gh" and "--repo" in args and "octo/demo" in args for args in calls)
    assert len(calls) == 3


def test_gh_does_not_retry_a_normal_failure() -> None:
    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "pull request already exists")

    provider = GhProvider("octo/demo", runner=runner, sleep=lambda _seconds: None)
    with pytest.raises(GhError):
        provider.open_pr("tf/run/alpha", "demo-base", "Alpha", "body")
