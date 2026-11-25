"""Provider and [pr] config. No network."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from treefleet.config import ConfigError, config_path, load_settings
from treefleet.pr.fake import FakeProvider
from treefleet.pr.gh import GhError, GhProvider
from treefleet.pr.loop import check_label

pytestmark = pytest.mark.phase11

ROOT = Path(__file__).resolve().parents[1]


def _secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")


def test_missing_pr_table_stays_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _secrets(monkeypatch)
    settings = load_settings()
    assert settings.pr.enabled is False
    assert settings.pr.allow_external_hooks is False
    assert settings.pr.poll_seconds == 15
    assert settings.pr.auto_merge is False


def test_poll_seconds_below_five_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _secrets(monkeypatch)
    text = config_path().read_text(encoding="utf-8")
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
    creates = {"n": 0}

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        if "list" in args:
            return subprocess.CompletedProcess(args, 0, "[]", "")
        if "view" in args:
            body = (
                '{"state":"OPEN","mergeable":"MERGEABLE","statusCheckRollup":[],'
                '"headRefOid":"abc","url":"https://github.com/octo/demo/pull/7"}'
            )
            return subprocess.CompletedProcess(args, 0, body, "")
        if "create" in args:
            creates["n"] += 1
            if creates["n"] < 3:
                return subprocess.CompletedProcess(args, 1, "", "HTTP 503 rate limit")
            return subprocess.CompletedProcess(args, 0, "https://github.com/octo/demo/pull/7\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    provider = GhProvider("octo/demo", runner=runner, sleep=lambda _seconds: None)
    opened = provider.open_pr("tf/run/alpha", "demo-base", "Alpha", "body")
    assert opened.number == 7
    assert opened.head_sha == "abc"
    assert all(args[0] == "gh" and "--repo" in args and "octo/demo" in args for args in calls)
    assert creates["n"] == 3


def test_uppercase_github_conclusions_count_as_passing() -> None:
    body = json.dumps(
        {
            "state": "OPEN",
            "mergeable": "MERGEABLE",
            "headRefOid": "a" * 40,
            "url": "https://github.com/octo/demo/pull/85",
            "statusCheckRollup": [{"name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}],
        }
    )

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, body, "")

    state = GhProvider("octo/demo", runner=runner).get_pr(85)
    assert state.check_runs[0].conclusion == "success"
    assert check_label(state) == "passing"


_ALLOWED = {
    "api": {"--paginate", "--jq"},
    "pr": {"--repo", "--base", "--head", "--title", "--body", "--json", "--squash", "--state"},
}


def test_every_provider_method_uses_the_flags_its_subcommand_allows() -> None:
    calls: list[list[str]] = []

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        if args[1] == "api":
            return subprocess.CompletedProcess(args, 0, "[]", "")
        if "view" in args:
            body = (
                '{"state":"OPEN","mergeable":"MERGEABLE","statusCheckRollup":'
                '[{"name":"test","status":"COMPLETED","conclusion":"failure"}],'
                '"headRefOid":"abc","url":"https://github.com/octo/demo/pull/7"}'
            )
            return subprocess.CompletedProcess(args, 0, body, "")
        if "create" in args or "comment" in args:
            return subprocess.CompletedProcess(args, 0, "https://github.com/octo/demo/pull/7\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    provider = GhProvider("octo/demo", runner=runner, sleep=lambda _seconds: None)
    provider.open_pr("tf/run/alpha", "demo-base", "Alpha", "body")
    provider.get_pr(7)
    provider.list_comments(7, 0)
    provider.post_comment(7, "note")
    provider.get_failed_check_summary(7)
    provider.merge(7)
    seen: set[str] = set()
    for args in calls:
        assert args[0] == "gh"
        sub = args[1]
        seen.add(sub)
        flags = [item for item in args if item.startswith("--")]
        assert sub in _ALLOWED
        assert set(flags) <= _ALLOWED[sub], args
        if sub == "api":
            assert "--repo" not in args
            assert any(item.startswith("repos/octo/demo/") for item in args)
        else:
            assert args[args.index("--repo") + 1] == "octo/demo"
    assert seen == {"api", "pr"}


def test_gh_does_not_retry_a_normal_failure() -> None:
    calls: list[list[str]] = []

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 1, "", "HTTP 422 pull request already exists")

    provider = GhProvider("octo/demo", runner=runner, sleep=lambda _seconds: None)
    with pytest.raises(GhError, match="already exists|422"):
        provider.open_pr("tf/run/alpha", "demo-base", "Alpha", "body")
    assert sum(1 for args in calls if "create" in args) == 1


def test_gh_retries_timeout_expired_then_raises() -> None:
    creates = {"n": 0}
    sleeps: list[float] = []
    retries: list[tuple[int, str]] = []

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        if "list" in args:
            return subprocess.CompletedProcess(args, 0, "[]", "")
        creates["n"] += 1
        raise subprocess.TimeoutExpired(cmd=args, timeout=120)

    provider = GhProvider("octo/demo", runner=runner, sleep=sleeps.append)
    with pytest.raises(GhError, match="timed out|TimeoutExpired|120"):
        provider.open_pr(
            "tf/run/alpha",
            "demo-base",
            "Alpha",
            "body",
            on_retry=lambda attempt, reason: retries.append((attempt, reason)),
        )
    assert creates["n"] == 3
    assert sleeps == [5.0, 20.0]
    assert retries == [(1, "timeout"), (2, "timeout")]


def test_open_pr_transient_then_success_one_pr() -> None:
    creates = {"n": 0}
    sleeps: list[float] = []

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        if "list" in args:
            return subprocess.CompletedProcess(args, 0, "[]", "")
        if "view" in args:
            body = (
                '{"state":"OPEN","mergeable":"MERGEABLE","statusCheckRollup":[],'
                '"headRefOid":"abc","url":"https://github.com/octo/demo/pull/7"}'
            )
            return subprocess.CompletedProcess(args, 0, body, "")
        if "create" in args:
            creates["n"] += 1
            if creates["n"] == 1:
                return subprocess.CompletedProcess(args, 1, "", "connection reset by peer")
            return subprocess.CompletedProcess(args, 0, "https://github.com/octo/demo/pull/7\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    provider = GhProvider("octo/demo", runner=runner, sleep=sleeps.append)
    opened = provider.open_pr("tf/run/alpha", "demo-base", "Alpha", "body")
    assert opened.number == 7
    assert creates["n"] == 2
    assert sleeps == [5.0]


def test_open_pr_two_transient_then_success() -> None:
    creates = {"n": 0}
    sleeps: list[float] = []
    retries: list[tuple[int, str]] = []

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        if "list" in args:
            return subprocess.CompletedProcess(args, 0, "[]", "")
        if "view" in args:
            body = (
                '{"state":"OPEN","mergeable":"MERGEABLE","statusCheckRollup":[],'
                '"headRefOid":"abc","url":"https://github.com/octo/demo/pull/9"}'
            )
            return subprocess.CompletedProcess(args, 0, body, "")
        if "create" in args:
            creates["n"] += 1
            if creates["n"] < 3:
                return subprocess.CompletedProcess(args, 1, "", "HTTP 503 bad gateway")
            return subprocess.CompletedProcess(args, 0, "https://github.com/octo/demo/pull/9\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    provider = GhProvider("octo/demo", runner=runner, sleep=sleeps.append)
    opened = provider.open_pr(
        "tf/run/alpha",
        "demo-base",
        "Alpha",
        "body",
        on_retry=lambda attempt, reason: retries.append((attempt, reason)),
    )
    assert opened.number == 9
    assert creates["n"] == 3
    assert sleeps == [5.0, 20.0]
    assert [r[0] for r in retries] == [1, 2]
    assert all(r[1] == "http_5xx" for r in retries)


def test_open_pr_three_transient_failures_raise() -> None:
    creates = {"n": 0}

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        if "list" in args:
            return subprocess.CompletedProcess(args, 0, "[]", "")
        if "create" in args:
            creates["n"] += 1
            return subprocess.CompletedProcess(args, 1, "", "TLS handshake timeout")
        return subprocess.CompletedProcess(args, 0, "", "")

    provider = GhProvider("octo/demo", runner=runner, sleep=lambda _seconds: None)
    with pytest.raises(GhError, match="TLS|timeout"):
        provider.open_pr("tf/run/alpha", "demo-base", "Alpha", "body")
    assert creates["n"] == 3


def test_open_pr_reuses_existing_after_timed_out_create() -> None:
    creates = {"n": 0}
    lists = {"n": 0}
    sleeps: list[float] = []

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        if "list" in args:
            lists["n"] += 1
            # After the timed-out create, GitHub already has the PR.
            return subprocess.CompletedProcess(
                args,
                0,
                '[{"number":11,"url":"https://github.com/octo/demo/pull/11"}]',
                "",
            )
        if "view" in args:
            body = (
                '{"state":"OPEN","mergeable":"MERGEABLE","statusCheckRollup":[],'
                '"headRefOid":"deadbeef","url":"https://github.com/octo/demo/pull/11"}'
            )
            return subprocess.CompletedProcess(args, 0, body, "")
        if "create" in args:
            creates["n"] += 1
            raise subprocess.TimeoutExpired(cmd=args, timeout=120)
        return subprocess.CompletedProcess(args, 0, "", "")

    provider = GhProvider("octo/demo", runner=runner, sleep=sleeps.append)
    opened = provider.open_pr("tf/run/alpha", "demo-base", "Alpha", "body")
    assert opened.number == 11
    assert opened.url.endswith("/11")
    assert creates["n"] == 1
    assert lists["n"] == 1
    # Existing PR is found before backoff sleep.
    assert sleeps == []


def test_open_pr_422_does_not_retry() -> None:
    creates = {"n": 0}

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        if "create" in args:
            creates["n"] += 1
            return subprocess.CompletedProcess(args, 1, "", "HTTP 422 no commits between base and head")
        return subprocess.CompletedProcess(args, 0, "[]", "")

    provider = GhProvider("octo/demo", runner=runner, sleep=lambda _s: (_ for _ in ()).throw(AssertionError("sleep")))
    with pytest.raises(GhError, match="422|no commits"):
        provider.open_pr("tf/run/alpha", "demo-base", "Alpha", "body")
    assert creates["n"] == 1


# --- PR-open reconciliation (AWS run b2597752: PR #126 was created, then a GraphQL error hid it) ---

_GRAPHQL = (
    "GraphQL: Something went wrong while executing your query on 2026-11-25T11:29:31Z. "
    "Please include `ABC` when reporting this issue."
)
_VIEW_BODY = (
    '{"state":"OPEN","mergeable":"MERGEABLE","statusCheckRollup":[],'
    '"headRefOid":"abc","url":"https://github.com/octo/demo/pull/7"}'
)


def _fake_gh(
    *,
    create: list[subprocess.CompletedProcess[str] | BaseException],
    listed: str = "[]",
    view_ok: bool = True,
) -> tuple[list[list[str]], object]:
    """A fake gh runner. `create` answers each `pr create` in order. Returns (calls, runner)."""
    calls: list[list[str]] = []
    queue = list(create)

    def runner(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(list(args))
        if "create" in args:
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        if "list" in args:
            return subprocess.CompletedProcess(args, 0, listed, "")
        if "view" in args:
            if view_ok:
                return subprocess.CompletedProcess(args, 0, _VIEW_BODY, "")
            return subprocess.CompletedProcess(args, 1, "", _GRAPHQL)
        return subprocess.CompletedProcess(args, 0, "", "")

    return calls, runner


def _failed(text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 1, "", text)


def _count(calls: list[list[str]], word: str) -> int:
    return sum(1 for args in calls if word in args)


def test_open_pr_graphql_error_on_create_adopts_the_pr_github_made() -> None:
    sleeps: list[float] = []
    listed = '[{"number":126,"url":"https://github.com/octo/demo/pull/126","headRefOid":"deadbeef"}]'
    calls, runner = _fake_gh(create=[_failed(_GRAPHQL)], listed=listed)
    provider = GhProvider("octo/demo", runner=runner, sleep=sleeps.append)  # type: ignore[arg-type]
    opened = provider.open_pr("tf/run/alpha", "demo-base", "Alpha", "body")
    assert (opened.number, opened.url, opened.head_sha, opened.adopted) == (
        126,
        "https://github.com/octo/demo/pull/126",
        "deadbeef",
        True,
    )
    assert _count(calls, "create") == 1  # no duplicate create
    assert sleeps == []
    lookup = next(args for args in calls if "list" in args)
    assert lookup[lookup.index("--head") + 1] == "tf/run/alpha"
    assert lookup[lookup.index("--state") + 1] == "open"
    assert lookup[lookup.index("--json") + 1] == "number,url,headRefOid"


def test_open_pr_create_succeeded_but_view_hits_graphql_error_keeps_the_pr() -> None:
    created = subprocess.CompletedProcess([], 0, "https://github.com/octo/demo/pull/126\n", "")
    calls, runner = _fake_gh(create=[created], view_ok=False)
    provider = GhProvider("octo/demo", runner=runner, sleep=lambda _s: None)  # type: ignore[arg-type]
    opened = provider.open_pr("tf/run/alpha", "demo-base", "Alpha", "body")
    assert opened.number == 126
    assert opened.head_sha == ""
    assert opened.adopted is False
    assert _count(calls, "create") == 1


def test_open_pr_graphql_error_with_no_pr_retries_then_fails() -> None:
    sleeps: list[float] = []
    retries: list[tuple[int, str]] = []
    calls, runner = _fake_gh(create=[_failed(_GRAPHQL)] * 3, listed="[]")
    provider = GhProvider("octo/demo", runner=runner, sleep=sleeps.append)  # type: ignore[arg-type]
    with pytest.raises(GhError, match="GraphQL"):
        provider.open_pr(
            "tf/run/alpha",
            "demo-base",
            "Alpha",
            "body",
            on_retry=lambda attempt, reason: retries.append((attempt, reason)),
        )
    assert _count(calls, "create") == 3
    assert sleeps == [5.0, 20.0]
    assert retries == [(1, "graphql"), (2, "graphql")]


def test_open_pr_already_exists_is_adopted_not_failed() -> None:
    sleeps: list[float] = []
    listed = '[{"number":42,"url":"https://github.com/octo/demo/pull/42","headRefOid":"cafe"}]'
    text = "HTTP 422: a pull request for branch tf/run/alpha into branch demo-base already exists"
    calls, runner = _fake_gh(create=[_failed(text)], listed=listed)
    provider = GhProvider("octo/demo", runner=runner, sleep=sleeps.append)  # type: ignore[arg-type]
    opened = provider.open_pr("tf/run/alpha", "demo-base", "Alpha", "body")
    assert (opened.number, opened.head_sha, opened.adopted) == (42, "cafe", True)
    assert _count(calls, "create") == 1
    assert sleeps == []


def test_open_pr_normal_success_is_unchanged() -> None:
    created = subprocess.CompletedProcess([], 0, "https://github.com/octo/demo/pull/7\n", "")
    calls, runner = _fake_gh(create=[created])
    provider = GhProvider("octo/demo", runner=runner, sleep=lambda _s: None)  # type: ignore[arg-type]
    opened = provider.open_pr("tf/run/alpha", "demo-base", "Alpha", "body")
    assert (opened.number, opened.head_sha, opened.adopted) == (7, "abc", False)
    assert _count(calls, "create") == 1
    assert _count(calls, "list") == 0  # no lookup when create works
