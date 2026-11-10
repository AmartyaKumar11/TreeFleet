"""One poller path, and the board follows the latest fact. No network."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import subprocess
from pathlib import Path

import pytest

from treefleet.config import load_settings
from treefleet.contracts import (
    Plan,
    ReviewVerdict,
    RunRequest,
    SelfReportedIsolation,
    StateKey,
    TaskSpec,
    WorkerResult,
)
from treefleet.events import EventSink, read_events
from treefleet.orchestrator.agents import RunBundle
from treefleet.orchestrator.review import _run_pr
from treefleet.pr.config import PrSettings
from treefleet.pr.fake import FakeProvider
from treefleet.pr.hooks import hooks_for_stage, hooks_for_watch
from treefleet.pr.loop import PollHooks, poll_once
from treefleet.pr.models import CheckRun, OpenPR
from treefleet.ui.lanes import tasks_from_snapshot

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "9b2cb269-events.jsonl"

pytestmark = pytest.mark.phase11

_SHA = "a" * 40


def test_stage_and_watch_build_the_same_hooks() -> None:
    fake = FakeProvider()
    fake.login = "ada"
    settings = PrSettings(enabled=True, repo="octo/demo")
    calls: list[str] = []

    def revise(task: str, feedback: str) -> str:
        calls.append(f"revise:{task}:{feedback}")
        return "abc"

    def resolve(task: str) -> str:
        calls.append(f"resolve:{task}")
        return f"resolved-{task}"

    def judge(task: str, comment: str, diff: str) -> tuple[str, str]:
        return "satisfied", f"{task}|{comment}|{len(diff)}"

    def emit(kind: str, task: str, payload: dict[str, object]) -> None:
        calls.append(f"{kind}:{task}:{payload.get('number')}")

    def diff(task: str) -> str:
        return f"diff --git a/{task} b/{task}\n+line\n"

    stage = hooks_for_stage(
        revise=revise,
        resolve_conflict=resolve,
        judge=judge,
        emit=emit,
        diff=diff,
        login="",
        provider=fake,
        settings=settings,
    )
    watch = hooks_for_watch(
        revise=revise,
        resolve_conflict=resolve,
        judge=judge,
        emit=emit,
        diff=diff,
        login="",
        provider=fake,
        settings=settings,
    )
    for hooks in (stage, watch):
        for name in ("judge", "revise", "resolve_conflict", "emit", "diff", "login"):
            assert getattr(hooks, name) is not None
        assert hooks.login == "ada"
        assert hooks.trusted == {"ada"}
        assert hooks.trust_checked is True
        assert hooks.trust_problem == ""
    assert stage.judge is not None and watch.judge is not None
    assert stage.diff is not None and watch.diff is not None
    assert stage.judge("notify", "please", "diff") == watch.judge("notify", "please", "diff")
    assert stage.revise("notify", "do it") == watch.revise("notify", "do it")
    assert stage.resolve_conflict("notify") == watch.resolve_conflict("notify")
    assert stage.diff("notify") == watch.diff("notify")
    stage.emit("pr.opened", "notify", {"number": 1})
    watch.emit("pr.opened", "notify", {"number": 1})
    assert [item for item in calls if item.startswith("pr.opened")] == ["pr.opened:notify:1", "pr.opened:notify:1"]
    from treefleet.cli import _pr_watch

    assert "hooks_for_stage(" in inspect.getsource(_run_pr)
    assert "hooks_for_watch(" in inspect.getsource(_pr_watch)
    assert "PollHooks(" not in inspect.getsource(_run_pr)
    assert "PollHooks(" not in inspect.getsource(_pr_watch)
    built = [
        path.name
        for path in (ROOT / "src" / "treefleet").rglob("*.py")
        if "PollHooks(" in path.read_text(encoding="utf-8")
    ]
    assert built == ["hooks.py"]


def _git(repo: Path, *args: str) -> None:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        shell=False,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(args[0] if args else "git")


def _worktree(path: Path) -> None:
    path.mkdir(parents=True)
    _git(path, "-c", "init.defaultBranch=demo-base", "init")
    _git(path, "remote", "add", "origin", "https://github.com/octo/demo.git")
    (path / "README").write_text("demo\n", encoding="utf-8")
    _git(path, "add", "README")
    _git(path, "-c", "user.email=treefleet@local", "-c", "user.name=TreeFleet", "commit", "-m", "base")
    (path / "tests").mkdir()
    (path / "tests" / "test_notifications.py").write_text("def test_one():\n    assert True\n", encoding="utf-8")
    _git(path, "add", "tests/test_notifications.py")
    _git(path, "-c", "user.email=treefleet@local", "-c", "user.name=TreeFleet", "commit", "-m", "tests")


class _Store:
    def __init__(self) -> None:
        self.stages: list[str] = []

    def set_stage(self, stage: str) -> None:
        self.stages.append(stage)

    def upsert_task(self, task_id: str, **fields: object) -> None:
        del task_id, fields


class _Ctx:
    def __init__(self, state: dict[str, object]) -> None:
        self.session = type("Session", (), {"state": state})()


def _lane_at(events: list[dict[str, object]], kind: str, nth: int = 1) -> str:
    seen = 0
    for index, event in enumerate(events, start=1):
        if event.get("type") != kind:
            continue
        seen += 1
        if seen == nth:
            cards = tasks_from_snapshot(events[:index], None, pr_enabled=True)
            return str(cards[0]["lane"])
    raise AssertionError(kind)


def _play(tmp_path: Path, verdicts: list[str]) -> tuple[FakeProvider, list[dict[str, object]], _Store]:
    os.environ.setdefault("DEEPSEEK_API_KEY", "local-test-key")
    os.environ.setdefault("TYPESAFE_API_KEY", "local-test-key")
    repo = tmp_path / "wt" / "run126" / "notify"
    _worktree(repo)
    settings = load_settings().model_copy(
        update={
            "pr": PrSettings(
                enabled=True,
                repo="octo/demo",
                base="demo-base",
                poll_seconds=5,
                max_wait_minutes=5,
                trusted_commenters=("ada",),
            )
        }
    )
    fake = FakeProvider()
    fake.login = "ada"
    pushed = {"n": 0}

    def revise(_task: str, _feedback: str) -> str:
        pushed["n"] += 1
        sha = ("c" if pushed["n"] == 1 else "d") * 40
        fake.set_state(
            fake.opened[0].number,
            head_sha=sha,
            check_runs=(CheckRun("pytest", "queued", ""),),
            mergeable="MERGEABLE",
        )
        return sha

    cursor = {"n": 0}

    def judge(_task: str, _comment: str, diff: str) -> tuple[str, str]:
        assert "test_notifications.py" in diff
        verdict = verdicts[min(cursor["n"], len(verdicts) - 1)]
        cursor["n"] += 1
        return verdict, "docstring added" if verdict == "satisfied" else "docstring missing"

    sink = EventSink(tmp_path / "runs")
    bundle = RunBundle(
        request=RunRequest(goal="Add unit tests for notifications", repo=str(repo)),
        settings=settings,
        repo=repo,
        run_id="run126",
        token="token",
        sink=sink,
        runs_root=tmp_path / "runs",
        worktree_root=tmp_path / "wt",
        port_start=8800,
        approve=None,
        fakes=None,
    )
    bundle.store = _Store()
    bundle.pr_provider = fake
    bundle.pr_push = lambda _task: "b" * 40
    bundle.pr_revise = revise
    bundle.pr_judge = judge
    bundle.pr_sleep = _script(fake)
    clock = {"t": 0.0}

    def now() -> float:
        clock["t"] += 1.0
        return clock["t"]

    bundle.pr_now = now
    bundle.pr_deadline = 40.0
    plan = Plan(
        goal="Add unit tests for notifications",
        rationale="One task covers the module.",
        tasks=[
            TaskSpec(
                id="notify",
                title="Notify tests",
                instructions="Add pytest tests for the notifications module.",
                allowed_paths=["tests/test_notifications.py"],
            )
        ],
    )
    result = WorkerResult(
        run_id="run126",
        task_id="notify",
        attempt=1,
        status="done",
        branch="tf/run126/notify",
        head_sha=_SHA,
        commit_made=True,
        model="deepseek/deepseek-flash",
        isolation=SelfReportedIsolation(
            pid=1, cwd=str(repo), toplevel=str(repo), branch="tf/run126/notify", mcp_root=str(repo)
        ),
    )
    verdict = ReviewVerdict(task_id="notify", verdict="approve", confidence=1, reasons=["in scope"])
    state = {
        StateKey.PLAN: plan.model_dump(mode="json"),
        StateKey.WORKER_RESULTS: {"notify": result.model_dump(mode="json")},
        StateKey.VERDICTS: [verdict.model_dump(mode="json")],
    }
    asyncio.run(_run_pr(bundle, _Ctx(state)))
    events, skipped = read_events(tmp_path / "runs" / "run126" / "events.jsonl")
    assert skipped == 0
    return fake, events, bundle.store


def _script(fake: FakeProvider):
    step = {"n": 0}

    def sleep(_seconds: float) -> None:
        step["n"] += 1
        number = fake.opened[0].number
        if step["n"] == 1:
            fake.set_state(
                number,
                check_runs=(CheckRun("pytest", "completed", "success"),),
                mergeable="MERGEABLE",
            )
        elif step["n"] == 2:
            fake.add_comment(number, "ada", "Please add a docstring to the first test.")
        elif step["n"] == 3:
            fake.set_state(
                number,
                check_runs=(CheckRun("pytest", "completed", "success"),),
                mergeable="MERGEABLE",
            )
        elif step["n"] == 4:
            fake.set_state(number, state="merged")

    return sleep


def test_in_run_stage_satisfies_one_comment_then_merges(tmp_path: Path) -> None:
    fake, events, store = _play(tmp_path, ["satisfied"])
    assert store.stages[0] == "awaiting_prs"
    assert _lane_at(events, "pr.opened") == "review"
    assert _lane_at(events, "pr.checks", 1) in {"review", "ready"}
    approved = []
    for event in events:
        payload = event.get("payload")
        if event.get("type") == "pr.checks" and isinstance(payload, dict) and payload.get("approved"):
            approved.append(event)
    assert approved
    assert _lane_at(events, "pr.revision_started") == "working"
    assert _lane_at(events, "pr.revision_pushed") == "review"
    assert _lane_at(events, "pr.merged") == "done"
    merged_at = next(index for index, event in enumerate(events) if event.get("type") == "pr.merged")
    ready = tasks_from_snapshot(events[:merged_at], None, pr_enabled=True)
    assert ready[0]["lane"] == "ready"
    bodies = [body for _number, body in fake.posted]
    assert len(bodies) == 1
    assert "changed tests/test_notifications.py (+2/-0)" in bodies[0]
    assert "changed no files" not in bodies[0]
    assert "Request: satisfied." in bodies[0]


def test_in_run_stage_unsatisfied_once_then_satisfied(tmp_path: Path) -> None:
    fake, events, _store = _play(tmp_path, ["unsatisfied", "satisfied"])
    assert len([event for event in events if event.get("type") == "pr.revision_started"]) == 2
    assert _lane_at(events, "pr.revision_started", 1) == "working"
    assert _lane_at(events, "pr.revision_started", 2) == "working"
    assert _lane_at(events, "pr.merged") == "done"
    bodies = [body for _number, body in fake.posted]
    assert len(bodies) == 2
    assert "not satisfied (docstring missing)" in bodies[0]
    assert "Request: satisfied." in bodies[1]
    assert all("changed no files" not in body for body in bodies)
    assert all("test_notifications.py" in body for body in bodies)


def _comment_item(fake: FakeProvider) -> OpenPR:
    opened = fake.open_pr("tf/run/notify", "demo-base", "Notify", "body")
    fake.set_state(opened.number, check_runs=(CheckRun("pytest", "completed", "success"),), head_sha="a" * 40)
    fake.add_comment(opened.number, "ada", "Please add a docstring.")
    return OpenPR(
        task_id="notify",
        number=opened.number,
        url=opened.url,
        head=opened.head,
        head_sha="a" * 40,
        status="approved_green",
        checks="passing",
    )


def _cards(item: OpenPR, hooks: PollHooks) -> dict[str, object]:
    events = [
        {"type": "pr.opened", "task_id": "notify", "payload": {"number": item.number, "url": item.url}},
        {"type": "review_verdict", "task_id": "notify", "payload": {"verdict": "approve"}},
        *[{"type": kind, "task_id": task, "payload": payload} for kind, task, payload in hooks.events],
    ]
    return tasks_from_snapshot(events, None, pr_enabled=True)[0]


def test_missing_judge_does_not_spend_a_round() -> None:
    fake = FakeProvider()
    item = _comment_item(fake)
    hooks = hooks_for_stage(
        revise=lambda *_args: "should-not-run",
        resolve_conflict=lambda *_args: None,
        judge=None,
        emit=lambda *_args: None,
        diff=lambda _task: "diff --git a/tests/test_notifications.py b/tests/test_notifications.py\n+x\n",
        provider=fake,
        settings=PrSettings(enabled=True, repo="octo/demo", trusted_commenters=("ada",)),
    )
    poll_once([item], fake, trusted={"ada"}, hooks=hooks)
    assert item.comments[0].rounds_used == 0
    assert not any(kind == "pr.revision_started" for kind, _task, _payload in hooks.events)
    unavailable = [payload for kind, _task, payload in hooks.events if kind == "acceptance.unavailable"]
    assert unavailable == [{"task": "notify", "reason": "reviewer unavailable"}]
    assert fake.posted == []
    card = _cards(item, hooks)
    assert card["lane"] == "needs"
    assert card["reason"] == "reviewer unavailable"


def test_raising_judge_does_not_try_again() -> None:
    fake = FakeProvider()
    item = _comment_item(fake)
    calls = {"n": 0}

    def revise(*_args: object) -> str:
        calls["n"] += 1
        return "c" * 40

    def judge(*_args: object) -> tuple[str, str]:
        raise RuntimeError("model down")

    hooks = hooks_for_watch(
        revise=revise,
        resolve_conflict=lambda *_args: None,
        judge=judge,
        emit=lambda *_args: None,
        diff=lambda _task: "diff --git a/tests/test_notifications.py b/tests/test_notifications.py\n+x\n",
        provider=fake,
        settings=PrSettings(enabled=True, repo="octo/demo", trusted_commenters=("ada",)),
    )
    poll_once([item], fake, trusted={"ada"}, hooks=hooks)
    assert calls["n"] == 1
    assert item.comments[0].rounds_used == 0
    assert len([kind for kind, _task, _payload in hooks.events if kind == "pr.revision_started"]) == 1
    assert [payload["reason"] for kind, _task, payload in hooks.events if kind == "acceptance.unavailable"] == [
        "reviewer unavailable"
    ]
    assert fake.posted == []
    card = _cards(item, hooks)
    assert card["lane"] == "needs"
    assert card["reason"] == "reviewer unavailable"


def test_replay_of_run_9b2cb269_puts_admin_in_ready() -> None:
    events = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]
    cards = {card["id"]: card for card in tasks_from_snapshot(events, None, pr_enabled=True)}
    assert cards["test-admin"]["lane"] == "ready"
    assert cards["test-admin"]["status"] == "failed"
    assert cards["test-notifications"]["lane"] == "needs"
    assert cards["test-notifications"]["reason"] == "no reviewer"


def test_checks_refresh_while_a_comment_needs_a_human() -> None:
    fake = FakeProvider()
    opened = fake.open_pr("tf/run/notify", "demo-base", "Notify", "body")
    item = OpenPR(
        task_id="notify",
        number=opened.number,
        url=opened.url,
        head=opened.head,
        head_sha="a" * 40,
        status="needs_review",
        checks="pending",
    )
    item.unresolved = True
    hooks = PollHooks(
        revise=lambda *_a: None,
        resolve_conflict=lambda *_a: None,
        emit=lambda *_a: None,
        judge=lambda *_a: ("satisfied", "ok"),
    )
    fake.set_state(
        item.number,
        head_sha="e" * 40,
        check_runs=(CheckRun("pytest", "completed", "success"),),
        mergeable="MERGEABLE",
    )
    poll_once([item], fake, trusted={"ada"}, hooks=hooks)
    assert item.checks == "passing"
    assert item.head_sha == "e" * 40
    assert item.status == "needs_review"
    events = [
        {"type": "pr.opened", "task_id": "notify", "payload": {"number": item.number, "url": item.url}},
        {
            "type": "pr.checks",
            "task_id": "notify",
            "payload": {"status": "needs_review", "reason": "round limit reached"},
        },
        *[
            {"type": kind, "task_id": task, "payload": payload}
            for kind, task, payload in hooks.events
            if kind == "pr.checks"
        ],
    ]
    card = tasks_from_snapshot(events, None, pr_enabled=True)[0]
    assert card["lane"] == "needs"
    assert card["pr_checks"] == "passing"


def test_own_comment_has_no_notice() -> None:
    events = [
        {"type": "pr.opened", "task_id": "notify", "payload": {"number": 91, "url": "https://example/91"}},
        {"type": "review_verdict", "task_id": "notify", "payload": {"verdict": "approve"}},
        {
            "type": "pr.checks",
            "task_id": "notify",
            "payload": {"checks": "passing", "approved": True, "number": 91},
        },
        {
            "type": "pr.comment_ignored",
            "task_id": "notify",
            "payload": {"reason": "untrusted", "comment_id": 1, "author": "eve"},
        },
        {
            "type": "pr.comment_ignored",
            "task_id": "notify",
            "payload": {"reason": "own", "comment_id": 2, "author": "ada"},
        },
        {
            "type": "pr.comment_ignored",
            "task_id": "notify",
            "payload": {"reason": "stale", "comment_id": 3, "author": "ada"},
        },
    ]
    mid = tasks_from_snapshot(events[:5], None, pr_enabled=True)[0]
    assert mid["notice"] == "comment ignored: untrusted"
    final = tasks_from_snapshot(events, None, pr_enabled=True)[0]
    assert final["notice"] == "comment ignored: stale"
    own_only = [*events[:3], events[4]]
    dropped = tasks_from_snapshot(own_only, None, pr_enabled=True)[0]
    assert dropped.get("notice") is None
