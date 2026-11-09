"""Provider surface. Tests use FakeProvider. GhProvider is the only GitHub caller."""

from __future__ import annotations

from typing import Protocol

from treefleet.pr.models import PR, Comment, PRState


class PullRequestProvider(Protocol):
    def open_pr(self, head: str, base: str, title: str, body: str) -> PR: ...

    def get_pr(self, number: int) -> PRState: ...

    def list_comments(self, number: int, since_id: int) -> list[Comment]: ...

    def post_comment(self, number: int, body: str) -> Comment: ...

    def get_failed_check_summary(self, number: int) -> str: ...

    def merge(self, number: int) -> None: ...
