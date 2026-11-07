"""TreeFleet contracts: the single source of truth for every message, state value and report.

Rules for this module:
  * Every cross-component payload (A2A, MCP tool result, ADK session state, API, SSE) is a model here.
  * Models are frozen and reject unknown fields, so drift fails loudly at the boundary.
  * Models that expose computed fields set ``extra="ignore"`` so a dumped payload can be re-validated.
  * No imports from adk, crewai, fastapi or any I/O library. This file stays pure.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Final, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
    model_validator,
)

SCHEMA_VERSION: Final = "v1"

# ---------------------------------------------------------------------------
# Primitive constrained types
# ---------------------------------------------------------------------------

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{1,38}$")]
RunId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]{6,12}$")]
ModelChoice = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:/\-]{1,100}$")]
Sha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]
Traceparent = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$")]

_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_PARENT_SEGMENT = re.compile(r"(^|/)\.\.(/|$)")


def _validate_rel_glob(value: str) -> str:
    """A path pattern must be relative, free of parent segments, and never target .git."""
    v = value.replace("\\", "/")
    if "\x00" in v or v.startswith("/") or _DRIVE_PREFIX.match(v) or _PARENT_SEGMENT.search(v):
        raise ValueError(f"path pattern must be relative and inside the worktree: {value!r}")
    if v == ".git" or v.startswith(".git/"):
        raise ValueError(".git is never an allowed path")
    return v


RelGlob = Annotated[str, StringConstraints(min_length=1, max_length=200), AfterValidator(_validate_rel_glob)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Constants shared by ADK session state and A2A metadata
# ---------------------------------------------------------------------------


class StateKey:
    """ADK session.state keys. Writers are listed in docs/CONTRACTS.md section 4."""

    REQUEST: Final = "request"
    GUARD_LOG: Final = "guard_log"
    REPO_MAP: Final = "repo_map"
    PLAN: Final = "plan"
    ROUTING: Final = "routing"
    ATTEMPTS: Final = "attempts"
    WORKER_RESULTS: Final = "worker_results"
    ISOLATION: Final = "isolation"
    VERDICTS: Final = "verdicts"
    INTEGRATION: Final = "integration"
    REPORT: Final = "report"


class WireMeta:
    """Metadata keys carried on A2A messages."""

    TYPE: Final = "treefleet/type"
    RUN_ID: Final = "treefleet/run_id"
    TASK_ID: Final = "treefleet/task_id"
    TRACEPARENT: Final = "traceparent"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Complexity(StrEnum):
    TRIVIAL = "trivial"
    MODERATE = "moderate"
    COMPLEX = "complex"


class WorkerStatus(StrEnum):
    QUEUED = "queued"
    SPAWNING = "spawning"
    WORKING = "working"
    TESTING = "testing"
    DONE = "done"
    FAILED = "failed"
    NEEDS_HUMAN = "needs_human"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ProgressKind(StrEnum):
    STATUS = "status"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    GUARD = "guard"
    LOG = "log"
    ERROR = "error"


class GuardStage(StrEnum):
    INPUT = "input"
    PLAN = "plan"
    TOOL_ARGS = "tool_args"
    TOOL_RESULT = "tool_result"
    TASK_OUTPUT = "task_output"
    FINAL_OUTPUT = "final_output"


# ---------------------------------------------------------------------------
# Request and plan
# ---------------------------------------------------------------------------

Scenario = Literal["custom", "same-file", "saboteur", "canary", "injection"]


class RunRequest(_Model):
    """Entry payload from the UI or CLI. ``checks`` maps a check name to a trusted command line."""

    goal: Annotated[str, StringConstraints(min_length=10, max_length=2000)]
    repo: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    base_ref: Annotated[str, StringConstraints(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9._/\-]+$")] = "HEAD"
    max_workers: int = Field(default=3, ge=1, le=6)
    checks: dict[Slug, Annotated[str, StringConstraints(min_length=1, max_length=300)]] = Field(
        default_factory=dict, max_length=8
    )
    notes: list[Annotated[str, StringConstraints(max_length=300)]] = Field(default_factory=list, max_length=20)
    scenario: Scenario = "custom"
    saboteur_task: Slug | None = None
    use_jev: bool = True
    worker_model: ModelChoice = "router"
    plan_approval: bool = True
    include_needs_review: bool = False


class TaskSpec(_Model):
    id: Slug
    title: Annotated[str, StringConstraints(min_length=3, max_length=120)]
    instructions: Annotated[str, StringConstraints(min_length=20, max_length=4000)]
    allowed_paths: list[RelGlob] = Field(min_length=1, max_length=20)
    denied_paths: list[RelGlob] = Field(default_factory=list, max_length=20)
    depends_on: list[Slug] = Field(default_factory=list, max_length=5)
    check_ids: list[Slug] = Field(default_factory=list, max_length=5)
    complexity: Complexity = Complexity.MODERATE
    model_override: ModelChoice | None = None

    @model_validator(mode="after")
    def _no_self_dependency(self) -> Self:
        if self.id in self.depends_on:
            raise ValueError(f"task {self.id} cannot depend on itself")
        return self


class Plan(_Model):
    goal: Annotated[str, StringConstraints(min_length=10, max_length=2000)]
    rationale: Annotated[str, StringConstraints(min_length=1, max_length=1500)]
    tasks: list[TaskSpec] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def _graph_is_valid(self) -> Self:
        ids = [t.id for t in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate task ids in plan")
        known = set(ids)
        for t in self.tasks:
            missing = set(t.depends_on) - known
            if missing:
                raise ValueError(f"task {t.id} depends on unknown tasks {sorted(missing)}")
        indegree = {t.id: len(t.depends_on) for t in self.tasks}
        children: dict[str, list[str]] = {i: [] for i in ids}
        for t in self.tasks:
            for dep in t.depends_on:
                children[dep].append(t.id)
        ready = [i for i, d in indegree.items() if d == 0]
        visited = 0
        while ready:
            node = ready.pop()
            visited += 1
            for child in children[node]:
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
        if visited != len(ids):
            raise ValueError("task dependency graph contains a cycle")
        return self

    def waves(self) -> list[list[str]]:
        """Topological layers. Tasks in one wave run in parallel."""
        remaining = {t.id: set(t.depends_on) for t in self.tasks}
        done: set[str] = set()
        layers: list[list[str]] = []
        while remaining:
            ready = sorted(i for i, deps in remaining.items() if deps <= done)
            layers.append(ready)
            done.update(ready)
            for i in ready:
                del remaining[i]
        return layers


class RouteDecision(_Model):
    task_id: Slug
    model: str
    complexity: Complexity
    confidence: Probability
    source: Literal["jev", "heuristic", "override", "run", "router", "default", "fallback"]
    cursor_model: ModelChoice | None = None


# ---------------------------------------------------------------------------
# Usage and cost
# ---------------------------------------------------------------------------


class TokenUsage(_Model):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)

    cache_write_tokens: int = Field(default=0, ge=0)

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


class CursorCall(_Model):
    """One cursor-agent invocation. Revise rounds keep every call."""

    round: int = Field(ge=1, le=5)
    model: str = ""
    usage: TokenUsage = Field(default_factory=TokenUsage)
    reported: bool = False


class ReviewerUsage(_Model):
    """One reviewer call. Priced as its own cost row."""

    task_id: Slug
    round: int = Field(ge=1, le=5)
    model: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    reported: bool = False


class CursorUsage(BaseModel):
    """Token counts from cursor-agent JSON. Names match the CLI."""

    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)

    input_tokens: int = Field(default=0, ge=0, alias="inputTokens")
    output_tokens: int = Field(default=0, ge=0, alias="outputTokens")
    cache_read_tokens: int = Field(default=0, ge=0, alias="cacheReadTokens")
    cache_write_tokens: int = Field(default=0, ge=0, alias="cacheWriteTokens")


class CursorRunResult(_Model):
    """What one headless cursor-agent invocation did. Built from the process, not from the model."""

    exit_code: int
    timed_out: bool
    duration_s: float = Field(ge=0)
    files_changed: list[str] = Field(default_factory=list)
    reverted_paths: list[str] = Field(default_factory=list)
    output_tail: Annotated[str, StringConstraints(max_length=2000)] = ""
    usage: CursorUsage | None = None


class OutputLine(_Model):
    """One redacted Cursor output line. Local only. Never a file body and never a secret."""

    line: Annotated[str, StringConstraints(max_length=200)]


class CursorToolEvent(_Model):
    """One Cursor tool start or finish. Path only. Never the file body."""

    tool: Annotated[str, StringConstraints(min_length=1, max_length=40)]
    path: Annotated[str, StringConstraints(max_length=300)] = ""
    ok: bool | None = None


class StepTiming(_Model):
    """One timed worker step. Payload of a worker.step event."""

    step: Literal["start", "deepseek", "cursor", "check", "commit"]
    duration_s: float = Field(ge=0)
    timed_out: bool = False
    name: Annotated[str, StringConstraints(max_length=100)] = ""
    output_tail: Annotated[str, StringConstraints(max_length=2000)] = ""


class WorkerDraft(_Model):
    """JSON the worker model returns. Plain code parses it. It is not a WorkerResult."""

    notes: Annotated[str, StringConstraints(min_length=1, max_length=3000)]


class CostLine(_Model):
    component: Literal["guard", "router", "planner", "worker", "reviewer", "reporter", "jev", "cursor"]
    model: str
    usage: TokenUsage
    usd: float = Field(ge=0)
    label: Annotated[str, StringConstraints(max_length=40)] = ""
    task_id: Slug | None = None


# ---------------------------------------------------------------------------
# A2A wire payloads (orchestrator <-> worker). Always carried as a DataPart.
# ---------------------------------------------------------------------------


class Limits(_Model):
    max_steps: int = Field(default=40, ge=1, le=200)
    max_output_chars: int = Field(default=8000, ge=500, le=100_000)
    max_file_bytes: int = Field(default=200_000, ge=1_000, le=5_000_000)
    timeout_s: int = Field(default=180, ge=30, le=7200)
    mcp_startup_s: int = Field(default=15, ge=1, le=120)


class TraceContext(_Model):
    traceparent: Traceparent
    tracestate: str | None = None


class UpstreamContext(_Model):
    task_id: Slug
    summary: Annotated[str, StringConstraints(max_length=3000)]
    branch: str
    head_sha: Sha


class WorkerTaskEnvelope(_Model):
    """Orchestrator -> worker. Carries everything a worker may know. Nothing else is shared."""

    schema_id: Literal["treefleet.worker_task.v1"] = "treefleet.worker_task.v1"
    run_id: RunId
    attempt: int = Field(default=1, ge=1, le=5)
    task: TaskSpec
    model: str
    cursor_model: ModelChoice | None = None
    checks: dict[Slug, str] = Field(default_factory=dict)
    base_sha: Sha | None = None
    limits: Limits = Field(default_factory=Limits)
    upstream: list[UpstreamContext] = Field(default_factory=list)
    revision_feedback: Annotated[str, StringConstraints(max_length=2000)] | None = None
    saboteur: bool = False
    trace: TraceContext | None = None


class ProgressEvent(_Model):
    """Worker -> orchestrator, sent as A2A status-update messages while the task is working."""

    schema_id: Literal["treefleet.progress.v1"] = "treefleet.progress.v1"
    run_id: RunId
    task_id: Slug
    seq: int = Field(ge=0)
    ts: datetime = Field(default_factory=_utcnow)
    kind: ProgressKind
    status: WorkerStatus | None = None
    message: Annotated[str, StringConstraints(max_length=2000)] = ""
    data: dict[str, Any] = Field(default_factory=dict)


class CheckResult(_Model):
    name: Slug
    command: str
    exit_code: int
    passed: bool
    stdout_tail: Annotated[str, StringConstraints(max_length=4000)] = ""
    stderr_tail: Annotated[str, StringConstraints(max_length=4000)] = ""
    duration_s: float = Field(default=0.0, ge=0)
    elided_by_jev: bool = False

    @model_validator(mode="after")
    def _passed_matches_exit_code(self) -> Self:
        if self.passed != (self.exit_code == 0):
            raise ValueError("passed must equal (exit_code == 0)")
        return self


class FileChange(_Model):
    path: str
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)


class SelfReportedIsolation(_Model):
    """What the worker claims. The orchestrator never trusts it; see IsolationProof."""

    pid: int = Field(ge=1)
    cwd: str
    toplevel: str
    branch: str
    mcp_root: str


class WorkerResult(_Model):
    """Worker -> orchestrator final A2A artifact."""

    schema_id: Literal["treefleet.worker_result.v1"] = "treefleet.worker_result.v1"
    run_id: RunId
    task_id: Slug
    attempt: int = Field(ge=1)
    status: Literal["done", "failed", "needs_human", "needs_review"]
    summary: Annotated[str, StringConstraints(max_length=3000)] = ""
    notes: Annotated[str, StringConstraints(max_length=3000)] = ""
    branch: str
    head_sha: Sha | None = None
    commit_made: bool = False
    files_changed: list[FileChange] = Field(default_factory=list, max_length=200)
    checks: list[CheckResult] = Field(default_factory=list)
    steps_used: int = Field(default=0, ge=0)
    duration_s: float = Field(default=0.0, ge=0)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    brain_usage: TokenUsage = Field(default_factory=TokenUsage)
    brain_usage_reported: bool = False
    cursor_model: str | None = None
    cursor_usage_reported: bool = False
    cursor_calls: list[CursorCall] = Field(default_factory=list, max_length=10)
    reverted_scope: list[str] = Field(default_factory=list, max_length=50)
    model: str
    isolation: SelfReportedIsolation
    error: Annotated[str, StringConstraints(max_length=2000)] | None = None
    cursor_message: Annotated[str, StringConstraints(max_length=2000)] = ""
    reverted_generated: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _status_is_consistent(self) -> Self:
        if self.status in {"done", "needs_review"} and not (self.commit_made and self.head_sha):
            raise ValueError(f"status={self.status} requires commit_made and head_sha")
        if self.status == "failed" and not self.error:
            raise ValueError("status=failed requires error")
        return self


class InputRequired(_Model):
    """Worker -> orchestrator when it needs a decision. Maps to the A2A input-required task state."""

    schema_id: Literal["treefleet.input_required.v1"] = "treefleet.input_required.v1"
    run_id: RunId
    task_id: Slug
    question: Annotated[str, StringConstraints(min_length=3, max_length=1000)]
    options: list[Annotated[str, StringConstraints(max_length=200)]] = Field(default_factory=list, max_length=6)


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------


class GuardDecision(_Model):
    stage: GuardStage
    allowed: bool
    detector: str
    reasons: list[str] = Field(default_factory=list, max_length=10)
    scores: dict[str, Probability] = Field(default_factory=dict)
    task_id: Slug | None = None
    ts: datetime = Field(default_factory=_utcnow)


# ---------------------------------------------------------------------------
# Review, isolation proof, integration
# ---------------------------------------------------------------------------


class ReviewVerdict(_Model):
    task_id: Slug
    verdict: Literal["approve", "revise", "reject"]
    confidence: Probability
    issues: list[Annotated[str, StringConstraints(max_length=500)]] = Field(default_factory=list, max_length=20)
    reasons: list[Annotated[str, StringConstraints(max_length=500)]] = Field(default_factory=list, max_length=20)
    required_changes: list[Annotated[str, StringConstraints(max_length=500)]] = Field(
        default_factory=list, max_length=20
    )
    scope_violation: bool = False
    revision_instructions: Annotated[str, StringConstraints(max_length=2000)] | None = None
    suggestions: list[Annotated[str, StringConstraints(max_length=500)]] = Field(default_factory=list, max_length=20)

    @model_validator(mode="before")
    @classmethod
    def _copy_review_text(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        reasons = data.get("reasons") or []
        if isinstance(reasons, str):
            reasons = [reasons]
        changes = data.get("required_changes") or []
        if isinstance(changes, str):
            changes = [changes]
        copied = dict(data)
        if reasons and not copied.get("issues"):
            copied["issues"] = list(reasons)
        if copied.get("issues") and not copied.get("reasons"):
            copied["reasons"] = list(copied["issues"])
        if changes and not copied.get("revision_instructions"):
            copied["revision_instructions"] = "\n".join(str(item) for item in changes)[:2000]
        return copied

    @model_validator(mode="after")
    def _revise_needs_instructions(self) -> Self:
        if self.verdict == "revise" and not (self.revision_instructions or self.required_changes):
            raise ValueError("verdict=revise requires revision_instructions")
        return self


class IsolationProof(_Model):
    """Computed by the orchestrator from git and the OS. Never from worker claims."""

    model_config = ConfigDict(extra="ignore")

    task_id: Slug
    worktree_path: str
    branch: str
    pid: int | None = None
    process_cwd_matches: bool
    toplevel_matches: bool
    registered_in_worktree_list: bool
    canary_absent: bool
    changes_within_allowed_paths: bool
    offending_paths: list[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ok(self) -> bool:
        return (
            self.process_cwd_matches
            and self.toplevel_matches
            and self.registered_in_worktree_list
            and self.canary_absent
            and self.changes_within_allowed_paths
        )


class MainCheckoutProof(_Model):
    model_config = ConfigDict(extra="ignore")

    status_before: list[str]
    status_after: list[str]
    head_before: Sha
    head_after: Sha
    canary_present: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ok(self) -> bool:
        return self.status_before == self.status_after and self.head_before == self.head_after and self.canary_present


class IsolationReport(_Model):
    model_config = ConfigDict(extra="ignore")

    tasks: list[IsolationProof]
    main: MainCheckoutProof

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ok(self) -> bool:
        return self.main.ok and all(t.ok for t in self.tasks)


class IntegrationConflict(_Model):
    path: str
    between: list[Slug] = Field(min_length=2)


class IntegrationReport(_Model):
    branch: str | None = None
    head_sha: Sha | None = None
    merged: list[Slug] = Field(default_factory=list)
    conflicts: list[IntegrationConflict] = Field(default_factory=list)
    skipped: list[Slug] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)
    excluded: list[Annotated[str, StringConstraints(max_length=300)]] = Field(default_factory=list, max_length=20)
    merge_command: str = ""
    user_branch: str = ""
    repo: str = ""


# ---------------------------------------------------------------------------
# Metrics and final report
# ---------------------------------------------------------------------------


class RunMetrics(_Model):
    model_config = ConfigDict(extra="ignore")

    wall_seconds: float = Field(ge=0)
    sequential_seconds: float = Field(ge=0)
    fleet_wall_seconds: float = Field(default=0, ge=0)
    task_seconds: float = Field(default=0, ge=0)
    costs: list[CostLine] = Field(default_factory=list)
    jev_calls: int = Field(default=0, ge=0)
    task_count: int = Field(default=0, ge=0)
    review_seconds: float = Field(default=0, ge=0)
    integration_seconds: float = Field(default=0, ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def speedup(self) -> float:
        return round(self.sequential_seconds / self.wall_seconds, 2) if self.wall_seconds > 0 else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def fleet_speedup(self) -> float:
        if self.fleet_wall_seconds <= 0:
            return 0.0
        return round(self.task_seconds / self.fleet_wall_seconds, 2)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_usd(self) -> float:
        return round(sum(c.usd for c in self.costs), 6)

    def definitions(self) -> str:
        """One line each for the end-to-end clock and the fleet clock."""
        single = self.task_count == 1
        if single:
            speedup = "n/a (single task)"
            fleet = "n/a (single task)"
        else:
            speedup = f"{self.speedup}x (sequential time divided by wall time)"
            fleet = f"{self.fleet_speedup}x (sum of task durations divided by fleet wall)"
        return "\n".join(
            [
                f"- Wall time: {self.wall_seconds:.1f}s (end-to-end clock, including planning)",
                "- Sequential time: "
                f"{self.sequential_seconds:.1f}s (pre-fleet overhead plus task, review and integration time)",
                f"- Speedup: {speedup}",
                f"- Fleet wall: {self.fleet_wall_seconds:.1f}s (time while workers run)",
                f"- Fleet speedup: {fleet}",
                f"- Review time: {self.review_seconds:.1f}s",
                f"- Integration time: {self.integration_seconds:.1f}s",
                f"- Total cost: ${self.total_usd:.4f}",
                f"- Jev calls: {self.jev_calls}",
            ]
        )


def assemble_metrics(
    *,
    wall: float,
    pre_fleet: float,
    fleet_wall: float,
    task_seconds: float,
    costs: list[CostLine] | None = None,
    jev_calls: int = 0,
    task_count: int = 0,
    review_seconds: float = 0,
    integration_seconds: float = 0,
) -> RunMetrics:
    """Sequential time is the work a one-at-a-time run would still have to do."""
    review = max(0.0, review_seconds)
    integration = max(0.0, integration_seconds)
    return RunMetrics(
        wall_seconds=max(0.0, wall),
        sequential_seconds=max(0.0, pre_fleet) + max(0.0, task_seconds) + review + integration,
        fleet_wall_seconds=max(0.0, fleet_wall),
        task_seconds=max(0.0, task_seconds),
        costs=costs or [],
        jev_calls=jev_calls,
        task_count=task_count,
        review_seconds=review,
        integration_seconds=integration,
    )


def isolation_overall(report: IsolationReport, results: list[WorkerResult]) -> str:
    """PASS, PASS with a contained revert, or FAIL. A leftover violation is FAIL."""
    main = report.main
    if main.status_before != main.status_after or main.head_before != main.head_after:
        return "FAIL"
    if not main.canary_present:
        return "FAIL"
    by_id = {item.task_id: item for item in results}
    contained = 0
    for proof in report.tasks:
        worktree_ok = proof.process_cwd_matches and proof.toplevel_matches and proof.registered_in_worktree_list
        if not worktree_ok or not proof.canary_absent or not proof.changes_within_allowed_paths:
            return "FAIL"
        result = by_id.get(proof.task_id)
        if result is not None and result.reverted_scope:
            contained += 1
    if contained == 1:
        return "PASS (1 contained violation)"
    if contained > 1:
        return f"PASS ({contained} contained violations)"
    return "PASS"


def scope_mark(proof: IsolationProof, result: WorkerResult | None) -> str:
    """Within-scope cell. A reverted edit that the next proof found clean says so."""
    if not proof.changes_within_allowed_paths:
        return "NO"
    if result is not None and result.reverted_scope:
        return "NO (reverted)"
    return "yes"


class FinalReport(_Model):
    schema_id: Literal["treefleet.final_report.v1"] = "treefleet.final_report.v1"
    run_id: RunId
    goal: str
    plan: Plan
    results: list[WorkerResult]
    verdicts: list[ReviewVerdict]
    isolation: IsolationReport
    integration: IntegrationReport | None = None
    routing: list[RouteDecision] = Field(default_factory=list)
    metrics: RunMetrics
    guard_decisions: list[GuardDecision] = Field(default_factory=list)
    notes: list[Annotated[str, StringConstraints(max_length=300)]] = Field(default_factory=list, max_length=20)

    def to_markdown(self) -> str:
        """Deterministic Markdown rendering. The LLM never writes the report."""
        verdicts = {v.task_id: v for v in self.verdicts}
        lines = [f"# TreeFleet run {self.run_id}", "", f"**Goal:** {self.goal}", ""]
        lines += [
            "## Tasks",
            "",
            "| Task | Status | Verdict | Revise rounds | Branch | Files | Checks |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in self.results:
            v = verdicts.get(r.task_id)
            checks = ", ".join(f"{c.name}:{'pass' if c.passed else 'fail'}" for c in r.checks) or "none"
            verdict = v.verdict if v else "n/a"
            rounds = max(0, r.attempt - 1)
            cells = f"| {r.task_id} | {r.status} | {verdict} | {rounds} | `{r.branch}` | {len(r.files_changed)} |"
            lines.append(f"{cells} {checks} |")
        for result in self.results:
            if result.cursor_message:
                lines.append(f"- {result.task_id} cursor: {result.cursor_message}")
            verdict = verdicts.get(result.task_id)
            if verdict is not None and verdict.suggestions:
                lines.append(f"- {result.task_id} suggestions: {'; '.join(verdict.suggestions)}")
            if result.error:
                lines.append(f"- {result.task_id} error: {result.error}")
            if result.status == "needs_review":
                why = "; ".join(verdict.reasons) if verdict is not None and verdict.reasons else "needs review"
                root = self.integration.repo if self.integration is not None and self.integration.repo else ""
                command = f"git -C {root} merge {result.branch}" if root else f"git merge {result.branch}"
                lines.append(f"- {result.task_id} needs review on `{result.branch}`: {why}. Merge: `{command}`")
        if self.routing:
            lines += ["", "## Models", ""]
            for route in self.routing:
                lines.append(f"- {route.task_id}: {route.cursor_model} ({route.source})")
        lines += ["", "## Isolation proof", "", f"**Overall:** {isolation_overall(self.isolation, self.results)}", ""]
        lines += [
            "| Task | Process cwd | Toplevel | Registered | Canary absent | Within scope |",
            "|---|---|---|---|---|---|",
        ]
        mark = {True: "yes", False: "NO"}
        by_id = {item.task_id: item for item in self.results}
        for p in self.isolation.tasks:
            cells = [
                mark[p.process_cwd_matches],
                mark[p.toplevel_matches],
                mark[p.registered_in_worktree_list],
                mark[p.canary_absent],
                scope_mark(p, by_id.get(p.task_id)),
            ]
            lines.append(f"| {p.task_id} | " + " | ".join(cells) + " |")
        m = self.isolation.main
        lines += [
            "",
            f"Main checkout unchanged: {mark[m.status_before == m.status_after and m.head_before == m.head_after]}; "
            f"canary still present: {mark[m.canary_present]}",
        ]
        if self.integration is not None:
            i = self.integration
            lines += ["", "## Integration", ""]
            if i.branch:
                lines.append(f"Branch: `{i.branch}`")
            lines.append(f"Merged: {', '.join(i.merged) or 'none'}")
            if i.excluded:
                lines.append("Excluded:")
                lines.extend(f"- {item}" for item in i.excluded)
            if i.checks:
                rendered = ", ".join(f"{c.name}:{'pass' if c.passed else 'fail'}" for c in i.checks)
            else:
                rendered = "none"
            lines.append(f"Post-merge checks: {rendered}")
            if i.merge_command:
                lines.append(f"Merge into `{i.user_branch or 'the current branch'}`: `{i.merge_command}`")
            for c in i.conflicts:
                lines.append(f"- Conflict in `{c.path}` between {', '.join(c.between)}")
        if self.notes:
            lines += ["", "## Notes", ""]
            lines.extend(f"- {note}" for note in self.notes)
        if self.metrics.costs:
            lines += [
                "",
                "## Cost",
                "",
                "| Role | Model | In | Out | Cache read | Cache write | USD | Note |",
                "|---|---|---|---|---|---|---|---|",
            ]
            for item in self.metrics.costs:
                lines.append(
                    f"| {item.component} | {item.model} | {item.usage.input_tokens} | "
                    f"{item.usage.output_tokens} | {item.usage.cached_input_tokens} | "
                    f"{item.usage.cache_write_tokens} | {item.usd:.6f} | {item.label} |"
                )
        lines += ["", "## Metrics", "", self.metrics.definitions()]
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# UI and log event envelope
# ---------------------------------------------------------------------------


class RunEvent(_Model):
    """One line of the run JSONL log and one SSE message. Produced by callbacks in both frameworks."""

    run_id: RunId
    seq: int = Field(ge=0)
    ts: datetime = Field(default_factory=_utcnow)
    source: Literal["api", "orchestrator", "worker", "guard", "jev", "git", "mcp"]
    # Vocabulary, including model_selected, is listed in docs/CONTRACTS.md section 5.
    type: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    task_id: Slug | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Wire helpers
# ---------------------------------------------------------------------------


def to_wire(model: _Model) -> dict[str, Any]:
    return model.model_dump(mode="json")


def from_wire[M: _Model](data: dict[str, Any], cls: type[M]) -> M:
    return cls.model_validate(data)
