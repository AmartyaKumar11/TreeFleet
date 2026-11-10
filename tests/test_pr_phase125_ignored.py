"""An ignored comment stays visible and is not read again after a restart."""

from __future__ import annotations

import pytest

from treefleet.orchestrator.review import pr_comment_cursor
from treefleet.pr.fake import FakeProvider
from treefleet.pr.loop import PollHooks, poll_once
from treefleet.pr.models import OpenPR
from treefleet.pr.safety import MARKER
from treefleet.ui.lanes import tasks_from_snapshot

pytestmark = pytest.mark.phase11


def test_ignored_comments_name_the_reason_and_advance_the_cursor() -> None:
    fake = FakeProvider()
    opened = fake.open_pr("tf/run/alpha", "demo-base", "Alpha", "body")
    own = fake.add_comment(opened.number, "treefleet", f"Revision 1\n{MARKER}", "2026-11-10T09:00:00Z")
    stranger = fake.add_comment(opened.number, "eve", "please change it", "2026-11-10T09:01:00Z")
    stale = fake.add_comment(opened.number, "ada", "old note", "2026-11-10T08:00:00Z")
    item = OpenPR(
        task_id="alpha",
        number=opened.number,
        url=opened.url,
        head=opened.head,
        head_sha=opened.head_sha,
        opened_at="2026-11-10T08:30:00Z",
    )
    hooks = PollHooks(revise=lambda *_a: "sha", resolve_conflict=lambda *_a: None, emit=lambda *_a: None)
    poll_once([item], fake, trusted={"ada"}, hooks=hooks)
    ignored = [payload for kind, _task, payload in hooks.events if kind == "pr.comment_ignored"]
    reasons = {payload["reason"]: payload for payload in ignored}
    assert set(reasons) == {"own", "untrusted", "stale"}
    assert reasons["own"]["comment_id"] == own.id
    assert reasons["untrusted"]["author"] == "eve"
    assert reasons["untrusted"]["comment_id"] == stranger.id
    assert reasons["untrusted"]["created_at"] == "2026-11-10T09:01:00Z"
    assert reasons["stale"]["comment_id"] == stale.id
    assert all("body" not in payload for payload in ignored)
    assert item.last_comment_id == max(own.id, stranger.id, stale.id)
    assert fake.list_comments(opened.number, item.last_comment_id) == []
    assert pr_comment_cursor({"comment_id": stranger.id}) == {"pr_last_comment_id": stranger.id}
    cards = tasks_from_snapshot(
        [
            {"type": "pr.opened", "task_id": "alpha", "payload": {"number": opened.number, "url": opened.url}},
            {"type": "review_verdict", "task_id": "alpha", "payload": {"verdict": "approve"}},
            {"type": "pr.checks_green", "task_id": "alpha", "payload": {"checks": "passing", "approved": True}},
            {
                "type": "pr.comment_ignored",
                "task_id": "alpha",
                "payload": {"reason": "untrusted", "comment_id": stranger.id, "author": "eve"},
            },
        ],
        None,
        pr_enabled=True,
    )
    assert cards[0]["lane"] == "ready"
    assert cards[0]["notice"] == "comment ignored: untrusted"
