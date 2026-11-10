"""In-memory provider. Every test and the eval use this, never gh."""

from __future__ import annotations

from treefleet.pr.models import PR, CheckRun, Comment, PRState


class FakeProvider:
    def __init__(self) -> None:
        self.opened: list[PR] = []
        self.comments: dict[int, list[Comment]] = {}
        self.states: dict[int, PRState] = {}
        self.merged: list[int] = []
        self.posted: list[tuple[int, str]] = []
        self.reads: list[tuple[str, int]] = []
        self._next = 1
        self._comment = 1
        self.login = ""

    def user(self) -> str:
        return self.login

    def open_pr(self, head: str, base: str, title: str, body: str) -> PR:
        number = self._next
        self._next += 1
        pr = PR(
            number=number,
            url=f"https://github.com/example/repo/pull/{number}",
            head=head,
            base=base,
            head_sha=f"sha{number}",
        )
        self.opened.append(pr)
        self.states[number] = PRState(
            number=number,
            state="open",
            mergeable="MERGEABLE",
            check_runs=(CheckRun("pytest", "queued", ""),),
            head_sha=pr.head_sha,
            url=pr.url,
        )
        self.comments[number] = []
        return pr

    def get_pr(self, number: int) -> PRState:
        self.reads.append(("get", number))
        if number not in self.states:
            raise KeyError(number)
        return self.states[number]

    def set_state(self, number: int, **fields: object) -> None:
        current = self.states[number]
        data = {
            "number": current.number,
            "state": current.state,
            "mergeable": current.mergeable,
            "check_runs": current.check_runs,
            "head_sha": current.head_sha,
            "url": current.url,
        }
        data.update(fields)
        self.states[number] = PRState(**data)  # type: ignore[arg-type]

    def add_comment(self, number: int, author: str, body: str, created_at: str = "") -> Comment:
        comment = Comment(id=self._comment, author=author, body=body, created_at=created_at)
        self._comment += 1
        self.comments[number].append(comment)
        return comment

    def list_comments(self, number: int, since_id: int) -> list[Comment]:
        self.reads.append(("comments", number))
        return [item for item in self.comments.get(number, []) if item.id > since_id]

    def post_comment(self, number: int, body: str) -> Comment:
        self.posted.append((number, body))
        return self.add_comment(number, "treefleet", body)

    def get_failed_check_summary(self, number: int) -> str:
        state = self.get_pr(number)
        lines = [
            f"{item.name} {item.conclusion or item.status}"
            for item in state.check_runs
            if item.conclusion not in {"", "success", "skipped"}
        ]
        return "\n".join(lines)[:1800]

    def merge(self, number: int) -> None:
        self.merged.append(number)
        self.set_state(number, state="merged", mergeable="UNKNOWN")
