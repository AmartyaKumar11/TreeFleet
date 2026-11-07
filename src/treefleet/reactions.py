"""Reaction policy. resolve() is pure. Callers emit reaction.fired."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

HARD_CAP = 5


class ReactionConfigError(RuntimeError):
    """A [reactions] table is not usable. load_settings turns this into ConfigError."""


# These names are not events. A toml key here is a hard error.
FROZEN_KEYS = {
    "guard": "a reaction cannot disable a guard",
    "guards": "a reaction cannot disable a guard",
    "isolation": "a reaction cannot bypass an isolation proof",
    "scope_revert": "a reaction cannot bypass scope revert",
    "merge_exclusion": "a reaction cannot include failed or needs_review tasks in the merge",
}

_NO_IGNORE = frozenset({"needs_review", "task_failed", "provider_error"})


class Event(StrEnum):
    checks_failed = "checks_failed"
    review_rejected = "review_rejected"
    merge_conflict = "merge_conflict"
    provider_error = "provider_error"
    task_failed = "task_failed"
    needs_review = "needs_review"
    approved_green = "approved_green"
    run_done = "run_done"


class Action(StrEnum):
    send_to_agent = "send_to_agent"
    escalate_model = "escalate_model"
    notify = "notify"
    needs_human = "needs_human"
    ignore = "ignore"


class Reaction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Action
    retries: int = Field(ge=0, le=HARD_CAP)
    escalate_after: int | None = None
    auto: bool = True

    @model_validator(mode="after")
    def _escalate_in_range(self) -> Self:
        if self.escalate_after is not None and not 0 <= self.escalate_after <= self.retries:
            raise ValueError(f"escalate_after must be from 0 to retries ({self.retries})")
        return self


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    action: Action
    attempt: int
    max_attempts: int
    escalate: bool


class ReactionTable(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    events: dict[Event, Reaction]
    defaults: tuple[str, ...]

    def get(self, event: Event) -> Reaction:
        return self.events[event]


def _reaction(action: Action, retries: int, escalate_after: int | None = None) -> Reaction:
    return Reaction(action=action, retries=retries, escalate_after=escalate_after, auto=True)


# Built-in defaults. review_rejected.retries is replaced with [revise] max_rounds at load
# when that event is not set in [reactions].
DEFAULTS: dict[Event, Reaction] = {
    Event.checks_failed: _reaction(Action.send_to_agent, 1),
    Event.review_rejected: _reaction(Action.send_to_agent, 1),
    Event.merge_conflict: _reaction(Action.send_to_agent, 1),
    Event.provider_error: _reaction(Action.send_to_agent, 3, 2),
    Event.task_failed: _reaction(Action.needs_human, 0),
    Event.needs_review: _reaction(Action.notify, 0),
    Event.approved_green: _reaction(Action.ignore, 0),
    Event.run_done: _reaction(Action.notify, 0),
}


def default_table(*, review_rounds: int | None = None) -> ReactionTable:
    events = {event: spec.model_copy() for event, spec in DEFAULTS.items()}
    if review_rounds is not None:
        capped = min(max(int(review_rounds), 0), HARD_CAP)
        events[Event.review_rejected] = events[Event.review_rejected].model_copy(update={"retries": capped})
    return ReactionTable(events=events, defaults=tuple(event.value for event in Event))


def _allowed_events() -> str:
    return ", ".join(event.value for event in Event)


def _allowed_actions() -> str:
    return ", ".join(action.value for action in Action)


def _check_safety(event: Event, spec: Reaction) -> None:
    if event.value in _NO_IGNORE and spec.action == Action.ignore:
        raise ReactionConfigError(f"reactions.{event.value}.action cannot be ignore. Allowed: {_allowed_actions()}.")
    if spec.retries > HARD_CAP:
        raise ReactionConfigError(f"reactions.{event.value}.retries must be from 0 to {HARD_CAP}.")


def _format_validation(event_name: str, exc: ValidationError) -> str:
    err = exc.errors()[0]
    loc = ".".join(str(part) for part in err["loc"])
    key = f"reactions.{event_name}.{loc}" if loc else f"reactions.{event_name}"
    err_type = err["type"]
    if "escalate_after" in str(err.get("msg", "")):
        return f"reactions.{event_name}.escalate_after must be an integer from 0 to retries, or omitted."
    if loc == "action" or err_type == "enum":
        return f"{key} must be one of {_allowed_actions()}."
    if loc == "retries":
        return f"{key} must be an integer from 0 to {HARD_CAP}."
    if loc == "escalate_after":
        return f"{key} must be an integer from 0 to retries, or omitted."
    if loc == "auto":
        return f"{key} must be a boolean."
    if err_type == "extra_forbidden":
        return f"{key} is not a reaction field. Allowed: action, retries, escalate_after, auto."
    msg = err.get("msg", "invalid")
    return f"{key} is invalid: {msg}."


def load_reactions(raw: object, *, review_rounds: int) -> ReactionTable:
    """Validate [reactions] and merge it onto the defaults. Raises ConfigError."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ReactionConfigError(f"reactions must be a table of events. Allowed events: {_allowed_events()}.")
    table = default_table(review_rounds=review_rounds)
    events = dict(table.events)
    default_names = set(table.defaults)
    for key, value in raw.items():
        name = str(key)
        if name in FROZEN_KEYS:
            raise ReactionConfigError(f"reactions.{name}: {FROZEN_KEYS[name]}. Allowed events: {_allowed_events()}.")
        if name not in Event._value2member_map_:
            raise ReactionConfigError(f"reactions.{name} is not a known event. Allowed events: {_allowed_events()}.")
        if not isinstance(value, dict):
            raise ReactionConfigError(f"reactions.{name} must be a table with action, retries, escalate_after, auto.")
        if "retries" in value and (isinstance(value["retries"], bool) or not isinstance(value["retries"], int)):
            raise ReactionConfigError(f"reactions.{name}.retries must be an integer from 0 to {HARD_CAP}.")
        if (
            "escalate_after" in value
            and value["escalate_after"] is not None
            and not isinstance(value["escalate_after"], int)
        ):
            raise ReactionConfigError(
                f"reactions.{name}.escalate_after must be an integer from 0 to retries, or omitted."
            )
        if "auto" in value and not isinstance(value["auto"], bool):
            raise ReactionConfigError(f"reactions.{name}.auto must be a boolean.")
        try:
            spec = Reaction.model_validate(value)
        except ValidationError as exc:
            raise ReactionConfigError(_format_validation(name, exc)) from exc
        event = Event(name)
        _check_safety(event, spec)
        events[event] = spec
        default_names.discard(name)
    loaded = ReactionTable(
        events=events, defaults=tuple(event.value for event in Event if event.value in default_names)
    )
    for event, spec in loaded.events.items():
        _check_safety(event, spec)
    return loaded


def resolve(event: Event, attempt: int, table: ReactionTable | None = None) -> Decision:
    """Decide the reaction for this 1-based attempt. No I/O."""
    spec = (table or default_table()).get(event)
    if event.value in _NO_IGNORE and spec.action == Action.ignore:
        raise ReactionConfigError(f"reactions.{event.value}.action cannot be ignore. Allowed: {_allowed_actions()}.")
    max_attempts = min(spec.retries, HARD_CAP)
    if spec.action in {Action.notify, Action.needs_human, Action.ignore}:
        return Decision(action=spec.action, attempt=attempt, max_attempts=max_attempts, escalate=False)
    if attempt > max_attempts:
        terminal = Action.needs_human if event == Event.provider_error else Action.ignore
        return Decision(action=terminal, attempt=attempt, max_attempts=max_attempts, escalate=False)
    escalate = spec.escalate_after is not None and attempt > spec.escalate_after
    action = Action.escalate_model if escalate else spec.action
    return Decision(action=action, attempt=attempt, max_attempts=max_attempts, escalate=escalate)


def reaction_payload(event: Event, decision: Decision, task: str | None) -> dict[str, Any]:
    return {
        "event": event.value,
        "task": task,
        "action": decision.action.value,
        "attempt": decision.attempt,
        "max_attempts": decision.max_attempts,
        "escalate": decision.escalate,
    }


def format_list(table: ReactionTable) -> str:
    lines: list[str] = []
    for event in Event:
        spec = table.get(event)
        mark = "default" if event.value in table.defaults else "configured"
        after = "-" if spec.escalate_after is None else str(spec.escalate_after)
        lines.append(
            f"{event.value} action={spec.action.value} retries={spec.retries} "
            f"escalate_after={after} auto={str(spec.auto).lower()} {mark}"
        )
    return "\n".join(lines)


def explain_events(lines: list[str]) -> str:
    """reaction.fired rows, in file order."""
    import json

    found: list[str] = []
    for raw in lines:
        if not raw.strip():
            continue
        item = json.loads(raw)
        if item.get("type") != "reaction.fired":
            continue
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        task = payload.get("task") or item.get("task_id") or "-"
        found.append(
            f"{payload.get('event')} task={task} action={payload.get('action')} "
            f"attempt={payload.get('attempt')} max_attempts={payload.get('max_attempts')} "
            f"escalate={payload.get('escalate')}"
        )
    return "\n".join(found)
