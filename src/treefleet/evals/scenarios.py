"""Load eval/scenarios.toml. No agent framework."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class Expectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    guard: str = "allow"
    task_min: int = 0
    task_max: int = 6
    statuses: list[str] = Field(default_factory=list)
    verdicts: list[str] = Field(default_factory=list)
    isolation: str = ""
    checks: str = "skip"
    cover: bool = False


class Variant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = ""
    flags: list[str] = Field(default_factory=list)


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    goal: str
    flags: list[str] = Field(default_factory=list)
    repeat: int = 1
    variant: list[Variant] = Field(default_factory=list)
    expect: Expectation = Field(default_factory=Expectation)

    def runs(self) -> list[tuple[str, list[str]]]:
        """One (label, flags) pair per variant, repeated `repeat` times."""
        variants = self.variant or [Variant(name="", flags=self.flags)]
        found: list[tuple[str, list[str]]] = []
        for _ in range(self.repeat):
            for item in variants:
                label = self.id if not item.name else f"{self.id}-{item.name}"
                found.append((label, item.flags or self.flags))
        return found


def load_scenarios(path: Path) -> list[Scenario]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [Scenario.model_validate(item) for item in data.get("scenario", [])]
