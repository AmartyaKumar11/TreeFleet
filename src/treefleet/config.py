"""Load treefleet.toml. Secrets come only from the environment."""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from treefleet.pr.config import PrConfigError, PrSettings, default_pr, load_pr
from treefleet.reactions import ReactionConfigError, ReactionTable, default_table, load_reactions

_SECRET_KEYS = ("deepseek_api_key", "typesafe_api_key", "cursor_api_key")


class MissingSecretError(RuntimeError):
    """A required environment variable is absent. The message never includes a value."""


class ConfigError(RuntimeError):
    """treefleet.toml is not usable."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RouterTiers(_Strict):
    default_tier: Literal["simple", "moderate", "complex"]
    simple: str
    moderate: str
    complex: str

    def model_for(self, complexity: str) -> str:
        """Map a Jev complexity to a Cursor model. `trivial` is the contract's low band."""
        key = "simple" if complexity == "trivial" else complexity
        if key not in ("simple", "moderate", "complex"):
            key = self.default_tier
        value: str = getattr(self, key)
        return value

    def fallback(self) -> str:
        value: str = getattr(self, self.default_tier)
        return value


class Models(_Strict):
    fast: str
    strong: str
    default_worker: str = "router"
    default: str = ""
    router: RouterTiers


class CursorCli(_Strict):
    cli: str
    list_flag: str
    model_flag: str
    workspace_flag: str
    sandbox: str
    flags: list[str] = Field(min_length=1)


class Thresholds(_Strict):
    injection: float
    abusive: float
    on_topic: float


class Workers(_Strict):
    port_start: int = Field(ge=1, le=65535)
    mcp_startup_s: int = Field(default=15, ge=1, le=120)
    max_workers: int = Field(default=3, ge=1, le=6)


class Paths(_Strict):
    worktree_windows: str
    worktree_other: str
    orchestrator_venv: str
    worker_venv: str


class Pricing(_Strict):
    litellm_url: str
    jev_usd_per_million_input: float = Field(ge=0)
    jev_source_url: str
    deepseek_input_per_million: float | None = None
    deepseek_output_per_million: float | None = None
    deepseek_flash_input_per_million: float | None = None
    deepseek_flash_cache_hit_per_million: float | None = None
    deepseek_flash_output_per_million: float | None = None
    deepseek_pro_input_per_million: float | None = None
    deepseek_pro_cache_hit_per_million: float | None = None
    deepseek_pro_output_per_million: float | None = None
    cursor_input_per_million: float | None = None
    cursor_output_per_million: float | None = None
    cursor_composer_input_per_million: float | None = None
    cursor_composer_cache_read_per_million: float | None = None
    cursor_composer_output_per_million: float | None = None
    cursor_composer_fast_input_per_million: float | None = None
    cursor_composer_fast_cache_read_per_million: float | None = None
    cursor_composer_fast_output_per_million: float | None = None


class Guards(_Strict):
    max_tasks: int = Field(default=6, ge=1, le=6)


class Revise(_Strict):
    max_rounds: int = Field(default=1, ge=0, le=2)
    round_budget_s: float = Field(default=240, gt=0, le=3600)


class Settings(_Strict):
    models: Models
    cursor: CursorCli
    thresholds: Thresholds
    workers: Workers
    paths: Paths
    pricing: Pricing
    guards: Guards = Field(default_factory=Guards)
    revise: Revise = Field(default_factory=Revise)
    reactions: ReactionTable = Field(default_factory=default_table)
    pr: PrSettings = Field(default_factory=default_pr)
    worktree_root: str
    orchestrator_python: str
    worker_python: str
    deepseek_api_key: str
    typesafe_api_key: str
    cursor_api_key: str | None = None

    @model_validator(mode="after")
    def _keys_are_nonempty(self) -> Self:
        if not self.deepseek_api_key.strip() or not self.typesafe_api_key.strip():
            raise ValueError("required secrets are missing")
        return self


def _required(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        raise MissingSecretError(f"Missing required environment variable {name}.")
    return value


def _python(venv: Path) -> Path:
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def load_settings(path: Path) -> Settings:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    reactions_raw = data.pop("reactions", None)
    pr_raw = data.pop("pr", None)
    leaked = [key for key in _SECRET_KEYS if key in data]
    if leaked:
        raise ConfigError(f"{leaked[0]} must be set with an environment variable, not treefleet.toml.")
    paths = data.get("paths")
    if not isinstance(paths, dict):
        raise ConfigError("treefleet.toml is missing [paths].")
    windows = sys.platform == "win32"
    root_key = "worktree_windows" if windows else "worktree_other"
    data["worktree_root"] = str(paths.get(root_key, ""))
    base = path.parent
    data["orchestrator_python"] = str(_python(base / str(paths.get("orchestrator_venv", ""))))
    data["worker_python"] = str(_python(base / str(paths.get("worker_venv", ""))))
    data["deepseek_api_key"] = _required("DEEPSEEK_API_KEY")
    data["typesafe_api_key"] = _required("TYPESAFE_API_KEY")
    cursor = os.environ.get("CURSOR_API_KEY")
    data["cursor_api_key"] = cursor if cursor else None
    try:
        settings = Settings.model_validate(data)
    except ValidationError:
        raise
    try:
        table = load_reactions(reactions_raw, review_rounds=settings.revise.max_rounds)
    except ReactionConfigError as exc:
        raise ConfigError(str(exc)) from exc
    try:
        pr = load_pr(pr_raw)
    except PrConfigError as exc:
        raise ConfigError(str(exc)) from exc
    return settings.model_copy(update={"reactions": table, "pr": pr})
