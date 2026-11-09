"""[pr] table. Absent or enabled=false leaves the rest of TreeFleet unchanged."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class PrConfigError(RuntimeError):
    """[pr] is not usable. load_settings turns this into ConfigError."""


class PrSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    repo: str = ""
    base: str = "demo-base"
    poll_seconds: int = Field(default=15, ge=5, le=3600)
    max_wait_minutes: int = Field(default=60, ge=1, le=24 * 60)
    trusted_commenters: tuple[str, ...] = ()
    auto_merge: bool = False
    allow_external_hooks: bool = False

    @model_validator(mode="after")
    def _repo_when_enabled(self) -> Self:
        if self.enabled and "/" not in self.repo:
            raise ValueError("pr.repo must be owner/name when pr.enabled is true")
        return self


def default_pr() -> PrSettings:
    return PrSettings()


def load_pr(raw: object) -> PrSettings:
    """Validate [pr]. A missing table is the default, which is off."""
    if raw is None:
        return default_pr()
    if not isinstance(raw, dict):
        raise PrConfigError("pr must be a table.")
    if "poll_seconds" in raw and (isinstance(raw["poll_seconds"], bool) or not isinstance(raw["poll_seconds"], int)):
        raise PrConfigError("pr.poll_seconds must be an integer of at least 5.")
    if "poll_seconds" in raw and isinstance(raw["poll_seconds"], int) and raw["poll_seconds"] < 5:
        raise PrConfigError("pr.poll_seconds must be an integer of at least 5.")
    commenters = raw.get("trusted_commenters", [])
    if not isinstance(commenters, list) or not all(isinstance(item, str) for item in commenters):
        raise PrConfigError("pr.trusted_commenters must be a list of logins.")
    payload = dict(raw)
    payload["trusted_commenters"] = tuple(commenters)
    try:
        return PrSettings.model_validate(payload)
    except ValidationError as exc:
        err = exc.errors()[0]
        loc = ".".join(str(part) for part in err["loc"]) or "pr"
        raise PrConfigError(f"pr.{loc} is invalid.") from exc
