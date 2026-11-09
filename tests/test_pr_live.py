"""Real gh flags against our own pull request. Skipped unless the gate is --live."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.live, pytest.mark.phase11]

ROOT = Path(__file__).resolve().parents[1]


def test_list_comments_and_get_pr_85() -> None:
    from treefleet.config import load_settings
    from treefleet.pr.gh import GhProvider

    settings = load_settings(ROOT / "treefleet.toml")
    if not settings.pr.enabled:
        pytest.skip("pr is off")
    provider = GhProvider(settings.pr.repo)
    state = provider.get_pr(85)
    comments = provider.list_comments(85, 0)
    assert state.number == 85
    assert any(comment.id == 6740866000 for comment in comments)
