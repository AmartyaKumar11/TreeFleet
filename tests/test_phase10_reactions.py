"""Phase 10 step 1: reaction defaults, overrides, and safety."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from treefleet.config import ConfigError, load_settings
from treefleet.contracts import CheckResult, Plan, ReviewVerdict, SelfReportedIsolation, TaskSpec, WorkerResult
from treefleet.orchestrator.review import review_loop, selected_branches
from treefleet.reactions import (
    HARD_CAP,
    Action,
    Event,
    Reaction,
    ReactionTable,
    default_table,
    explain_events,
    format_list,
    load_reactions,
    resolve,
)

pytestmark = pytest.mark.phase10

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def _secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")


def _result(**update: object) -> WorkerResult:
    base: dict[str, object] = {
        "run_id": "abc12345",
        "task_id": "alpha",
        "attempt": 1,
        "status": "done",
        "branch": "tf/abc12345/alpha",
        "head_sha": SHA,
        "commit_made": True,
        "model": "deepseek/deepseek-flash",
        "isolation": SelfReportedIsolation(
            pid=1, cwd="C:/wt", toplevel="C:/wt", branch="tf/abc12345/alpha", mcp_root="C:/wt"
        ),
    }
    base.update(update)
    return WorkerResult(**base)  # type: ignore[arg-type]


def _check_failed() -> WorkerResult:
    return _result(
        status="failed",
        error="a named check failed",
        commit_made=False,
        head_sha=None,
        checks=[CheckResult(name="pytest", command="pytest", exit_code=1, passed=False)],
    )


def test_defaults_match_the_inventory() -> None:
    table = default_table(review_rounds=1)
    specs = {event: table.get(event) for event in Event}
    assert specs[Event.checks_failed].action == Action.send_to_agent
    assert specs[Event.checks_failed].retries == 1
    assert specs[Event.review_rejected].action == Action.send_to_agent
    assert specs[Event.review_rejected].retries == 1
    assert specs[Event.merge_conflict].action == Action.send_to_agent
    assert specs[Event.merge_conflict].retries == 1
    assert specs[Event.provider_error].retries == 3
    assert specs[Event.provider_error].escalate_after == 2
    assert resolve(Event.provider_error, 1, table).action == Action.send_to_agent
    assert resolve(Event.provider_error, 3, table).escalate is True
    assert resolve(Event.provider_error, 4, table).action == Action.needs_human
    assert specs[Event.task_failed].action == Action.needs_human
    assert specs[Event.task_failed].retries == 0
    assert specs[Event.needs_review].action == Action.notify
    assert specs[Event.approved_green].action == Action.ignore
    assert specs[Event.run_done].action == Action.notify


def test_loaded_toml_marks_every_event_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _secrets(monkeypatch)
    text = format_list(load_settings(ROOT / "treefleet.toml").reactions)
    for event in Event:
        assert f"{event.value} " in text
        assert "configured" not in text.split(event.value, 1)[1].split("\n", 1)[0]


@pytest.mark.asyncio
async def test_checks_failed_retries_follow_config() -> None:
    async def review(task_id: str, _result: WorkerResult, _round: int) -> ReviewVerdict:
        return ReviewVerdict(task_id=task_id, verdict="approve", confidence=1, reasons=["ok"])

    async def run(retries: int) -> int:
        calls = 0

        async def rerun(_task_id: str, result: WorkerResult, _feedback: str, attempt: int) -> WorkerResult:
            nonlocal calls
            calls += 1
            return result.model_copy(update={"attempt": attempt})

        events = dict(default_table().events)
        events[Event.checks_failed] = Reaction(action=Action.send_to_agent, retries=retries, auto=True)
        table = ReactionTable(events=events, defaults=())
        await review_loop(["alpha"], {"alpha": _check_failed()}, review=review, rerun=rerun, reactions=table)
        return calls

    assert await run(0) == 0
    assert await run(2) == 2


def test_invalid_config_names_the_key() -> None:
    cases = [
        ({"nope": {"action": "ignore"}}, "reactions.nope"),
        ({"isolation": {"action": "ignore"}}, "reactions.isolation"),
        ({"checks_failed": {"action": "dance"}}, "reactions.checks_failed.action"),
        ({"checks_failed": {"action": "send_to_agent", "retries": 6}}, "reactions.checks_failed.retries"),
        ({"checks_failed": {"action": "send_to_agent", "retries": "two"}}, "reactions.checks_failed.retries"),
        ({"checks_failed": {"action": "send_to_agent", "retries": 1, "escalate_after": 4}}, "escalate_after"),
        ({"checks_failed": {"action": "send_to_agent", "retries": 1, "auto": "yes"}}, "reactions.checks_failed.auto"),
        ({"needs_review": {"action": "ignore", "retries": 0}}, "reactions.needs_review.action"),
        ({"task_failed": {"action": "ignore", "retries": 0}}, "reactions.task_failed.action"),
        ({"provider_error": {"action": "ignore", "retries": 0}}, "reactions.provider_error.action"),
    ]
    for raw, key in cases:
        with pytest.raises(Exception) as caught:
            load_reactions(raw, review_rounds=1)
        assert key in str(caught.value)


def test_retries_cap_at_five_even_if_built_by_hand() -> None:
    events = dict(default_table().events)
    events[Event.checks_failed] = Reaction.model_construct(
        action=Action.send_to_agent, retries=9, escalate_after=None, auto=True
    )
    table = ReactionTable.model_construct(events=events, defaults=())
    assert resolve(Event.checks_failed, 1, table).max_attempts == HARD_CAP
    assert resolve(Event.checks_failed, 6, table).action != Action.send_to_agent


def test_reactions_cannot_drop_needs_review_from_the_merge() -> None:
    plan = Plan(
        goal="Add one sentence to the readme.",
        rationale="one task",
        tasks=[
            TaskSpec(
                id="alpha",
                title="Readme line",
                instructions="Add one sentence to README.md for this demo.",
                allowed_paths=["README.md"],
            )
        ],
    )
    held = _result(status="needs_review")
    verdict = ReviewVerdict(task_id="alpha", verdict="reject", confidence=1, reasons=["open"])
    branches, reasons = selected_branches(plan, {"alpha": held}, {"alpha": verdict})
    assert branches == {}
    assert "alpha" in reasons
    failed = _result(status="failed", error="boom", commit_made=False, head_sha=None)
    branches, _reasons = selected_branches(plan, {"alpha": failed}, {"alpha": verdict})
    assert "alpha" not in branches


@pytest.mark.asyncio
async def test_reaction_fired_fields() -> None:
    seen: list[dict[str, Any]] = []

    async def review(task_id: str, _result: WorkerResult, _round: int) -> ReviewVerdict:
        return ReviewVerdict(task_id=task_id, verdict="approve", confidence=1, reasons=["ok"])

    async def rerun(_task_id: str, result: WorkerResult, _feedback: str, attempt: int) -> WorkerResult:
        return result.model_copy(update={"attempt": attempt})

    def emit(kind: str, _task: str, _round: int, payload: dict[str, Any]) -> None:
        if kind == "reaction.fired":
            seen.append(payload)

    await review_loop(
        ["alpha"],
        {"alpha": _check_failed()},
        review=review,
        rerun=rerun,
        emit=emit,
    )
    fired = seen[0]
    assert fired["event"] == "checks_failed"
    assert fired["task"] == "alpha"
    assert fired["action"] == "send_to_agent"
    assert fired["attempt"] == 1
    assert fired["max_attempts"] == 1
    assert fired["escalate"] is False
    text = explain_events(
        [
            '{"type":"review_started","payload":{}}',
            '{"type":"reaction.fired","task_id":"alpha","payload":'
            + '{"event":"checks_failed","task":"alpha","action":"send_to_agent",'
            + '"attempt":1,"max_attempts":1,"escalate":false}}',
        ]
    )
    assert text.startswith("checks_failed task=alpha")


def test_startup_rejects_a_bad_reactions_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _secrets(monkeypatch)
    path = tmp_path / "treefleet.toml"
    path.write_text(
        (ROOT / "treefleet.toml").read_text(encoding="utf-8") + '\n[reactions.nope]\naction = "ignore"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as caught:
        load_settings(path)
    assert "reactions.nope" in str(caught.value)
