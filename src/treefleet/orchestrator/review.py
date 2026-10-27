"""Reviewer, revise loop, and integrator. The reviewer has no mutating tool."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from treefleet.contracts import Plan, ReviewVerdict, RunEvent, StateKey, WorkerResult
from treefleet.git.worktrees import GitError, git_out
from treefleet.guards.paths import path_violation
from treefleet.guards.text import redact
from treefleet.mcp_servers.gitops_server import READ_ONLY_TOOLS
from treefleet.orchestrator.agents import (
    RunBundle,
    _adk_event,
    _complete,
    _json_text,
    prove,
    read_plan,
    read_results,
)

# Two re-dispatches. A third revise verdict rejects the task.
MAX_REVISIONS = 2

REVIEW_INSTRUCTION = (
    "You are a reviewer. You have no write tool and no mutating git tool. "
    "Return one JSON object and nothing else. "
    "Fields: verdict (approve, revise, or reject), confidence from 0 to 1, "
    "reasons (a list of strings), required_changes (a list of strings). "
    "If checks failed, do not approve. revise requires at least one required change."
)

ReviewFn = Callable[[str, WorkerResult, int], Awaitable[ReviewVerdict]]
RerunFn = Callable[[str, WorkerResult, str, int], Awaitable[WorkerResult]]
ProveFn = Callable[[dict[str, WorkerResult]], None]
EmitFn = Callable[[str, str, int, dict[str, Any]], None]


def reviewer_tools() -> tuple[str, ...]:
    """The only tools a reviewer may call. Mutating git stays in plain Python."""
    return tuple(sorted(READ_ONLY_TOOLS))


def screen_review_call(name: str, args: dict[str, Any] | None = None) -> str | None:
    """Phase 5 tool-arg rule for the reviewer: read-only names, no parent or secret paths."""
    if name not in READ_ONLY_TOOLS:
        return "mutating tool"
    for value in (args or {}).values():
        if isinstance(value, str):
            why = path_violation(value)
            if why:
                return why
    return None


def screen_review_result(text: str) -> str:
    cleaned, _count = redact(text)
    return cleaned


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [str(item)[:500] for item in value if str(item).strip()][:20]


def verdict_from_text(text: str, task_id: str) -> ReviewVerdict:
    data = json.loads(_json_text(text))
    if not isinstance(data, dict):
        raise ValueError("verdict is not an object")
    raw = str(data.get("verdict") or "").lower()
    if raw not in {"approve", "revise", "reject"}:
        raise ValueError("verdict is missing")
    reasons = _strings(data.get("reasons") or data.get("issues"))
    changes = _strings(data.get("required_changes"))
    if not changes and isinstance(data.get("revision_instructions"), str):
        changes = _strings([data["revision_instructions"]])
    if raw == "revise" and not changes:
        raw = "reject"
        reasons = reasons or ["revise had no required changes"]
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    return ReviewVerdict(
        task_id=task_id,  # type: ignore[arg-type]
        verdict=raw,  # type: ignore[arg-type]
        confidence=confidence,
        reasons=reasons,
        issues=reasons,
        required_changes=changes,
        revision_instructions=("\n".join(changes)[:2000] or None),
    )


async def verdict_with_repair(
    complete: Callable[[str], Awaitable[str]],
    task_id: str,
    prompt: str,
) -> ReviewVerdict:
    """Parse the reviewer JSON. One repair retry, then reject."""
    text = await complete(prompt)
    try:
        return verdict_from_text(text, task_id)
    except (ValueError, ValidationError, json.JSONDecodeError):
        pass
    text = await complete(prompt + "\nThe previous reply was not valid JSON. Return the JSON object only.")
    try:
        return verdict_from_text(text, task_id)
    except (ValueError, ValidationError, json.JSONDecodeError) as exc:
        reason = f"verdict JSON was not valid: {exc}"[:500]
        return ReviewVerdict(
            task_id=task_id,  # type: ignore[arg-type]
            verdict="reject",
            confidence=0,
            reasons=[reason],
            issues=[reason],
        )


def _forced_reject(task_id: str, source: ReviewVerdict | str) -> ReviewVerdict:
    if isinstance(source, str):
        reasons = [source[:500] or "rejected"]
        confidence = 1.0
    else:
        reasons = list(source.reasons or source.issues) or ["revise limit reached"]
        confidence = source.confidence
    return ReviewVerdict(
        task_id=task_id,  # type: ignore[arg-type]
        verdict="reject",
        confidence=confidence,
        reasons=reasons[:20],
        issues=reasons[:20],
    )


async def review_loop(
    task_ids: list[str],
    results: dict[str, WorkerResult],
    *,
    review: ReviewFn,
    rerun: RerunFn,
    prove: ProveFn | None = None,
    emit: EmitFn | None = None,
    max_revisions: int = MAX_REVISIONS,
) -> tuple[dict[str, WorkerResult], dict[str, ReviewVerdict]]:
    """One reviewer per task, in parallel. A revise re-runs the same branch, at most twice."""
    import asyncio

    def _emit(kind: str, task_id: str, round_no: int, payload: dict[str, Any]) -> None:
        if emit is not None:
            emit(kind, task_id, round_no, payload)

    async def _one(task_id: str) -> tuple[str, WorkerResult, ReviewVerdict]:
        result = results[task_id]
        verdict: ReviewVerdict | None = None
        for round_no in range(1, max_revisions + 2):
            _emit("review_started", task_id, round_no, {})
            try:
                verdict = await review(task_id, result, round_no)
            except Exception as exc:
                verdict = _forced_reject(task_id, type(exc).__name__)
                _emit("review_verdict", task_id, round_no, {"verdict": "reject"})
                break
            if verdict.verdict == "revise" and round_no > max_revisions:
                verdict = _forced_reject(task_id, verdict)
                _emit("review_verdict", task_id, round_no, {"verdict": "reject"})
                break
            _emit("review_verdict", task_id, round_no, {"verdict": verdict.verdict})
            if verdict.verdict != "revise":
                break
            feedback = "\n".join(verdict.required_changes) or (verdict.revision_instructions or "")
            _emit("revise_started", task_id, round_no, {})
            try:
                result = await rerun(task_id, result, feedback[:2000], result.attempt + 1)
            except Exception as exc:
                verdict = _forced_reject(task_id, str(exc))
                break
            if prove is not None:
                snapshot = dict(results)
                snapshot[task_id] = result
                prove(snapshot)
        assert verdict is not None
        return task_id, result, verdict

    ids = [task_id for task_id in task_ids if task_id in results]
    pairs = await asyncio.gather(*[_one(task_id) for task_id in ids])
    updated = dict(results)
    verdicts: dict[str, ReviewVerdict] = {}
    for task_id, result, verdict in pairs:
        updated[task_id] = result
        verdicts[task_id] = verdict
    return updated, verdicts


def approved_branches(
    results: dict[str, WorkerResult],
    verdicts: dict[str, ReviewVerdict],
) -> dict[str, str]:
    found: dict[str, str] = {}
    for task_id, result in results.items():
        verdict = verdicts.get(task_id)
        if verdict and verdict.verdict == "approve" and result.status == "done" and result.head_sha:
            found[task_id] = result.branch
    return found


def gitops_snapshot(repo: Path, base: str, branch: str) -> str:
    """Read-only diff. The same facts as gitops diff. No merge, commit, or worktree add."""
    try:
        stat = git_out(["diff", "--stat", f"{base}...{branch}"], repo)
        patch = git_out(["diff", "--unified=1", f"{base}...{branch}"], repo)
    except GitError as exc:
        stat, patch = str(exc), ""
    cleaned, _count = redact(f"{stat}\n{patch}"[:8000])
    return cleaned


def _prompt(task: Any, result: WorkerResult, round_no: int, snapshot: str, note: str) -> str:
    checks = ", ".join(f"{item.name}:{'pass' if item.passed else 'fail'}" for item in result.checks) or "none"
    return (
        f"Task {task.id}: {task.instructions}\n"
        f"Round {round_no}. Status {result.status}. Checks: {checks}.\n"
        f"Allowed: {task.allowed_paths}.\n"
        f"{note}\n"
        f"Read-only gitops diff:\n{snapshot[:4000]}\n"
        "If checks failed, verdict is revise or reject."
    )


def _jev_note(bundle: RunBundle, task: Any, diff: str) -> str:
    """Jev in_scope before the reviewer. A Jev failure does not block the review."""
    jev = getattr(bundle, "jev", None)
    if jev is None or not bundle.request.use_jev:
        return ""
    try:
        scores = jev.probabilities(
            f"{task.instructions}\n{diff[:1500]}",
            {"in_scope": "The diff stays within the task instructions."},
        )
    except Exception:
        bundle.sink.emit_sync(
            RunEvent(
                run_id=bundle.run_id,
                seq=0,
                source="jev",
                type="guard_degraded",
                task_id=task.id,
                payload={"stage": "review", "detector": "jev"},
            )
        )
        return "Jev in_scope unavailable."
    return f"Jev in_scope noul={float(scores.get('in_scope', 0)):.2f}"


def _emit(bundle: RunBundle, kind: str, task_id: str, round_no: int, payload: dict[str, Any]) -> None:
    bundle.sink.emit_sync(
        RunEvent(
            run_id=bundle.run_id,
            seq=0,
            source="orchestrator",
            type=kind,
            task_id=task_id,  # type: ignore[arg-type]
            payload={"round": round_no, **payload},
        )
    )


async def _review_one(bundle: RunBundle, plan: Plan, result: WorkerResult, round_no: int) -> ReviewVerdict:
    fakes = bundle.fakes
    if fakes is not None:
        script = getattr(fakes, "review_script", None)
        if script:
            return verdict_from_text(script.pop(0), result.task_id)
        if result.status == "done" and all(item.passed for item in result.checks):
            return ReviewVerdict(
                task_id=result.task_id,
                verdict="approve",
                confidence=1,
                reasons=["scripted approve"],
            )
        why = (result.error or "checks failed")[:500]
        return ReviewVerdict(task_id=result.task_id, verdict="reject", confidence=1, reasons=[why], issues=[why])
    task = next(item for item in plan.tasks if item.id == result.task_id)
    return await _live_verdict(bundle, task, result, round_no)


async def _live_verdict(bundle: RunBundle, task: Any, result: WorkerResult, round_no: int) -> ReviewVerdict:
    from google.adk.models.lite_llm import LiteLlm

    from treefleet.orchestrator.callbacks import UsageBox

    snapshot = gitops_snapshot(bundle.repo, bundle.request.base_ref, result.branch)
    prompt = _prompt(task, result, round_no, snapshot, _jev_note(bundle, task, snapshot))
    llm = LiteLlm(model=bundle.settings.models.strong)
    box = UsageBox()

    async def complete(text: str) -> str:
        return await _complete(
            llm,
            text,
            sink=bundle.sink,
            run_id=bundle.run_id,
            usage=box,
            agent_name="reviewer",
            instruction=REVIEW_INSTRUCTION,
            output_key="verdict",
            tool_guard=screen_review_call,
        )

    verdict = await verdict_with_repair(complete, result.task_id, prompt)
    if verdict.verdict == "approve" and any(not item.passed for item in result.checks):
        reason = "checks failed"
        return ReviewVerdict(task_id=result.task_id, verdict="reject", confidence=1, reasons=[reason], issues=[reason])
    return verdict


async def _redispatch(
    bundle: RunBundle,
    task_id: str,
    feedback: str,
    attempt: int,
) -> WorkerResult:
    from treefleet.orchestrator.fleet import dispatch

    proc = bundle.workers.get(task_id)
    envelope = bundle.envelopes.get(task_id)
    if proc is None or envelope is None:
        raise RuntimeError("worker is gone")
    fresh = envelope.model_copy(update={"attempt": attempt, "revision_feedback": feedback[:2000] or None})
    bundle.envelopes[task_id] = fresh
    result, _note = await dispatch(proc.url, bundle.token, fresh)
    return result


def _base_agent() -> type:
    from google.adk.agents import BaseAgent

    return BaseAgent


BaseAgent = _base_agent()


class ReviewerAgent(BaseAgent):
    bundle: Any

    async def _run_async_impl(self, ctx: Any) -> AsyncGenerator[Any, None]:
        if not self.bundle.approved:
            return
        plan = read_plan(ctx.session.state)
        results = read_results(ctx.session.state)
        holder: dict[str, Any] = {}

        def prove_now(snapshot: dict[str, WorkerResult]) -> None:
            holder["isolation"] = prove(self.bundle, plan, snapshot)

        updated, verdicts = await review_loop(
            [task.id for task in plan.tasks],
            results,
            review=lambda task_id, result, round_no: _review_one(self.bundle, plan, result, round_no),
            rerun=lambda task_id, _result, feedback, attempt: _redispatch(self.bundle, task_id, feedback, attempt),
            prove=prove_now,
            emit=lambda kind, task_id, round_no, payload: _emit(self.bundle, kind, task_id, round_no, payload),
        )
        delta: dict[str, Any] = {
            StateKey.WORKER_RESULTS: {key: value.model_dump(mode="json") for key, value in updated.items()},
            StateKey.VERDICTS: [item.model_dump(mode="json") for item in verdicts.values()],
            StateKey.ATTEMPTS: {key: value.attempt for key, value in updated.items()},
        }
        if "isolation" in holder:
            delta[StateKey.ISOLATION] = holder["isolation"].model_dump(mode="json")
        yield _adk_event(ctx, self.name, delta)


class IntegratorAgent(BaseAgent):
    bundle: Any

    async def _run_async_impl(self, ctx: Any) -> AsyncGenerator[Any, None]:
        if not self.bundle.approved:
            return
        from treefleet.git.merge import integrate

        plan = read_plan(ctx.session.state)
        results = read_results(ctx.session.state)
        verdicts = {
            item.task_id: item
            for item in (ReviewVerdict.model_validate(raw) for raw in ctx.session.state.get(StateKey.VERDICTS, []))
        }
        order = [task_id for wave in plan.waves() for task_id in wave]
        branches: dict[str, str] = {}
        reasons: dict[str, str] = {}
        for task_id in order:
            verdict = verdicts.get(task_id)
            result = results.get(task_id)
            if verdict and verdict.verdict == "approve" and result and result.status == "done" and result.head_sha:
                branches[task_id] = result.branch
                continue
            if verdict is None:
                reasons[task_id] = "not approved"
            elif verdict.verdict == "reject":
                reasons[task_id] = "rejected: " + (verdict.reasons[0] if verdict.reasons else "rejected")
            else:
                reasons[task_id] = verdict.verdict
        report = integrate(
            self.bundle.repo,
            self.bundle.run_id,
            order,
            branches,
            self.bundle.request.base_ref,
            worktree_root=self.bundle.worktree_root,
            checks=dict(self.bundle.request.checks) or None,
            reasons=reasons,
        )
        yield _adk_event(ctx, self.name, {StateKey.INTEGRATION: report.model_dump(mode="json")})
