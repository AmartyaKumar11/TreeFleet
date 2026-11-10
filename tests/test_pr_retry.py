"""Re-arm only a start failure or a check that could not run. The live run is not touched."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from treefleet.cli import _pr_retry
from treefleet.pr.fake import FakeProvider
from treefleet.pr.loop import PollHooks, poll_once
from treefleet.pr.models import Comment, OpenPR, TrackedComment
from treefleet.pr.retry import rearm_comment
from treefleet.sessions import load_run

pytestmark = pytest.mark.phase11

FIXTURE = Path(__file__).resolve().parent / "fixtures"
RUN = FIXTURE / "59180665-retry-run.json"
EVENTS = FIXTURE / "59180665-retry-events.jsonl"
COMMENT = 6760374466


def _events() -> list[dict[str, object]]:
    return [json.loads(line) for line in EVENTS.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_rehearsal_comment_recorded_as_no_commit_is_refused() -> None:
    record = load_run(RUN)
    changed, message, updated = rearm_comment(record, _events(), 87)
    assert changed is False
    assert updated is record
    assert str(COMMENT) in message
    assert "revision made no commit" in message


def test_start_failed_resets_the_rehearsal_comment_and_is_idempotent() -> None:
    record = load_run(RUN)
    events = [
        *_events(),
        {
            "type": "worker.start_failed",
            "task_id": "add-notifications-unit-tests",
            "payload": {
                "task": "add-notifications-unit-tests",
                "reason": "ModuleNotFoundError: No module named 'crewai'",
            },
        },
    ]
    changed, message, updated = rearm_comment(record, events, 87)
    assert changed is True
    assert message == f"comment {COMMENT} on pull request 87 reset to open, rounds_used 0"
    task = next(row for row in updated.tasks if row.pr_number == 87)
    comment = next(row for row in task.comments if row.id == COMMENT)
    assert comment.status == "open"
    assert comment.rounds_used == 0
    assert task.pr_status == "open"
    assert task.pr_last_comment_id == COMMENT - 1
    again, second, same = rearm_comment(updated, events, 87)
    assert again is False
    assert second == f"comment {COMMENT} already open, rounds_used 0"
    assert same is updated


def test_check_unavailable_can_be_rearmed() -> None:
    record = load_run(RUN)
    events = [
        {
            "type": "check.unavailable",
            "task_id": "add-notifications-unit-tests",
            "payload": {"task": "add-notifications-unit-tests", "reason": "pytest: module missing"},
        }
    ]
    changed, _message, updated = rearm_comment(record, events, 87)
    assert changed is True
    task = next(row for row in updated.tasks if row.pr_number == 87)
    assert task.comments[0].rounds_used == 0


def test_retry_command_writes_a_copy_and_not_the_live_run(tmp_path: Path) -> None:
    live = Path(__file__).resolve().parents[1] / "runs" / "59180665" / "run.json"
    before = live.read_bytes()
    run_dir = tmp_path / "runs" / "59180665"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_bytes(RUN.read_bytes())
    events = _events() + [
        {
            "type": "worker.start_failed",
            "task_id": "add-notifications-unit-tests",
            "payload": {
                "task": "add-notifications-unit-tests",
                "reason": "ModuleNotFoundError: No module named 'crewai'",
            },
        }
    ]
    (run_dir / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in events), encoding="utf-8")
    code = _pr_retry(["59180665", "87"], tmp_path / "runs")
    assert code == 0
    written = load_run(run_dir / "run.json")
    comment = next(row for row in written.tasks if row.pr_number == 87).comments[0]
    assert comment.status == "open"
    assert comment.rounds_used == 0
    assert live.read_bytes() == before


def test_next_poll_starts_one_round_for_an_open_comment() -> None:
    fake = FakeProvider()
    opened = fake.open_pr("tf/59180665/add-notifications-unit-tests", "demo-rehearsal", "Notes", "body")
    fake.comments[opened.number].append(
        Comment(id=COMMENT, author="AmartyaKumar11", body="Please add a one-line module docstring.")
    )
    item = OpenPR(
        task_id="add-notifications-unit-tests",
        number=opened.number,
        url=opened.url,
        head=opened.head,
        head_sha=opened.head_sha,
        last_comment_id=COMMENT - 1,
        comments=[TrackedComment(id=COMMENT, body="", status="open", rounds_used=0)],
    )
    calls = {"n": 0}

    def revise(_task_id: str, _feedback: str) -> str:
        calls["n"] += 1
        return "abc123"

    hooks = PollHooks(
        revise=revise,
        resolve_conflict=lambda _task: None,
        emit=lambda *_args: None,
        judge=lambda *_args: ("satisfied", "matches the request"),
        diff=lambda _task: "tests/test_notifications.py | 1 +",
    )
    poll_once([item], fake, trusted={"AmartyaKumar11"}, hooks=hooks)
    assert calls["n"] == 1
    assert len(item.comments) == 1
    assert item.comments[0].status == "satisfied"
