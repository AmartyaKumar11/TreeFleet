"""Reviewer, revise loop, and integrator. The reviewer has no mutating tool."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from treefleet.contracts import CheckResult, Plan, ReviewVerdict, RunEvent, StateKey, WorkerResult
from treefleet.git.worktrees import GitError, git_out
from treefleet.guards.paths import path_violation
from treefleet.guards.text import redact
from treefleet.mcp_servers.gitops_server import READ_ONLY_TOOLS
from treefleet.orchestrator.agents import (
    RunBundle,
    _adk_event,
    _complete,
    _json_text,
    _phase,
    prove,
    read_plan,
    read_results,
)

# One re-dispatch by default. treefleet.toml [revise] max_rounds is the live value.
MAX_REVISIONS = 1
_CHECK_FEEDBACK_CAP = 1800


def check_failure_feedback(checks: list[CheckResult]) -> str:
    """Name each failing check and a redacted, size-capped output tail for revision_feedback."""
    chunks: list[str] = []
    for item in checks:
        if item.passed:
            continue
        raw = (item.stdout_tail or item.stderr_tail or "").strip()
        cleaned, _count = redact(raw)
        tail = cleaned[-800:] if cleaned else "no output"
        chunks.append(f"Check {item.name} failed (exit {item.exit_code}). Output:\n{tail}")
    text = "Fix the failing checks, then leave a clean diff.\n\n" + "\n\n".join(chunks)
    return text[:_CHECK_FEEDBACK_CAP]


REVIEW_INSTRUCTION = (
    "You are a reviewer. You have no write tool and no mutating git tool. "
    "Return one JSON object and nothing else. "
    "Fields: verdict (approve, revise, or reject), confidence from 0 to 1, "
    "reasons (a list of strings), required_changes (a list of strings), "
    "suggestions (a list of strings). "
    "Revise or reject only for failing checks, files outside scope, an empty or irrelevant diff, "
    "tests that assert nothing, or a function named in the task with no coverage. "
    "Stronger assertions and extra cases go in suggestions and do not block approval. "
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


def apply_rubric(
    verdict: ReviewVerdict,
    *,
    checks_failed: bool,
    outside_scope: bool,
    empty_diff: bool,
    test_text: str,
    functions: list[str],
) -> ReviewVerdict:
    """Quality notes become suggestions. Only a real gap can revise or reject."""
    uncovered = [name for name in functions if name not in test_text]
    asserts_nothing = bool(test_text) and "assert " not in test_text
    blockers: list[str] = []
    if checks_failed:
        blockers.append("checks failed")
    if outside_scope:
        blockers.append("files outside scope")
    if empty_diff:
        blockers.append("empty diff")
    if asserts_nothing:
        blockers.append("tests assert nothing")
    if uncovered:
        blockers.append("no coverage for " + ", ".join(uncovered))
    if blockers:
        kind = "reject" if checks_failed or outside_scope or empty_diff else "revise"
        changes = blockers if kind == "revise" else []
        return verdict.model_copy(
            update={
                "verdict": kind,
                "reasons": blockers,
                "issues": blockers,
                "required_changes": changes,
                "revision_instructions": "\n".join(changes) or None,
                "suggestions": [],
            }
        )
    ideas: list[str] = []
    for item in [*verdict.suggestions, *verdict.reasons, *verdict.required_changes]:
        text = item.strip()
        if text and text not in ideas:
            ideas.append(text[:500])
    return verdict.model_copy(
        update={
            "verdict": "approve",
            "reasons": ["checks passed and the diff is in scope"],
            "issues": ["checks passed and the diff is in scope"],
            "required_changes": [],
            "revision_instructions": None,
            "suggestions": ideas[:20],
        }
    )


def _holdable(result: WorkerResult) -> bool:
    """A committed in-scope diff with passing checks can wait for a person."""
    if result.status not in {"done", "needs_review"}:
        return False
    if not (result.head_sha and result.commit_made and result.files_changed):
        return False
    if result.reverted_scope or any(not item.passed for item in result.checks):
        return False
    return "scope" not in (result.error or "")


def _needs_review(result: WorkerResult, fresh: WorkerResult) -> WorkerResult:
    return result.model_copy(
        update={
            "status": "needs_review",
            "error": None,
            "attempt": fresh.attempt,
            "cursor_message": fresh.cursor_message or result.cursor_message,
        }
    )


def verdict_reason(verdict: ReviewVerdict) -> str:
    parts = [item for item in verdict.reasons if item]
    if verdict.revision_instructions:
        parts.append(verdict.revision_instructions)
    return "; ".join(parts)[:300]


def verdict_unparseable(verdict: ReviewVerdict) -> bool:
    return any(str(item).startswith("verdict JSON was not valid") for item in verdict.reasons)


async def review_or_escalate(
    complete_for: Callable[[str, str], Awaitable[str]],
    models: list[str],
    task_id: str,
    prompt: str,
) -> tuple[ReviewVerdict, str]:
    """Flash first. v4-pro only after flash is still unparseable following its repair."""
    verdict = await verdict_with_repair(lambda text: complete_for(models[0], text), task_id, prompt)
    used = models[0]
    if verdict_unparseable(verdict) and len(models) > 1:
        verdict = await verdict_with_repair(lambda text: complete_for(models[1], text), task_id, prompt)
        used = models[1]
    return verdict, used


async def review_loop(
    task_ids: list[str],
    results: dict[str, WorkerResult],
    *,
    review: ReviewFn,
    rerun: RerunFn,
    prove: ProveFn | None = None,
    emit: EmitFn | None = None,
    max_revisions: int = MAX_REVISIONS,
    diff_of: Callable[[WorkerResult], str] | None = None,
    timings: dict[str, float] | None = None,
    round_budget_s: float | None = None,
) -> tuple[dict[str, WorkerResult], dict[str, ReviewVerdict]]:
    """One reviewer per task, in parallel. A revise re-runs the same branch within the round budget."""
    import asyncio

    def _emit(kind: str, task_id: str, round_no: int, payload: dict[str, Any]) -> None:
        if emit is not None:
            emit(kind, task_id, round_no, payload)

    async def _one(task_id: str) -> tuple[str, WorkerResult, ReviewVerdict]:
        import time

        result = results[task_id]
        error = result.error or ""
        if result.status == "failed" and (error.startswith("worker made no changes") or error.startswith("upstream ")):
            verdict = ReviewVerdict(
                task_id=task_id,  # type: ignore[arg-type]
                verdict="reject",
                confidence=1,
                reasons=[error[:500]],
            )
            _emit("review_verdict", task_id, 1, {"verdict": "reject", "reason": error[:300]})
            return task_id, result, verdict
        if any(not item.passed for item in result.checks):
            feedback = check_failure_feedback(result.checks)
            _emit("revise_started", task_id, 0, {"reason": "checks_failed"})
            revise_started = time.monotonic()
            try:
                fresh = await rerun(task_id, result, feedback, result.attempt + 1)
            except Exception as exc:
                verdict = _forced_reject(task_id, str(exc))
                _emit("review_verdict", task_id, 0, {"verdict": "reject", "reason": verdict_reason(verdict)})
                return task_id, result, verdict
            if timings is not None:
                timings["revise"] = timings.get("revise", 0.0) + (time.monotonic() - revise_started)
            result = fresh.model_copy(
                update={
                    "cursor_calls": [*list(result.cursor_calls), *fresh.cursor_calls],
                    "duration_s": result.duration_s + fresh.duration_s,
                    "reverted_scope": [*list(result.reverted_scope), *fresh.reverted_scope],
                }
            )
            if prove is not None:
                snapshot = dict(results)
                snapshot[task_id] = result
                prove(snapshot)
        verdict: ReviewVerdict | None = None
        for round_no in range(1, max_revisions + 2):
            _emit("review_started", task_id, round_no, {})
            started = time.monotonic()
            try:
                verdict = await review(task_id, result, round_no)
            except Exception as exc:
                verdict = _forced_reject(task_id, type(exc).__name__)
                _emit("review_verdict", task_id, round_no, {"verdict": "reject", "reason": verdict_reason(verdict)})
                if timings is not None:
                    timings["review"] = timings.get("review", 0.0) + (time.monotonic() - started)
                break
            if timings is not None:
                timings["review"] = timings.get("review", 0.0) + (time.monotonic() - started)
            if verdict.verdict == "revise" and round_no > max_revisions:
                if _holdable(result):
                    result = _needs_review(result, result)
                    reasons = ["review rounds exhausted", *list(verdict.reasons)]
                    verdict = verdict.model_copy(
                        update={"verdict": "reject", "reasons": reasons[:20], "issues": reasons[:20]}
                    )
                else:
                    verdict = _forced_reject(task_id, verdict)
                _emit(
                    "review_verdict",
                    task_id,
                    round_no,
                    {
                        "verdict": verdict.verdict,
                        "reason": verdict_reason(verdict),
                        "status": result.status,
                    },
                )
                break
            _emit(
                "review_verdict",
                task_id,
                round_no,
                {
                    "verdict": verdict.verdict,
                    "reason": verdict_reason(verdict),
                    "suggestions": list(verdict.suggestions),
                },
            )
            if verdict.verdict != "revise":
                break
            feedback = "\n".join(verdict.required_changes) or (verdict.revision_instructions or "")
            _emit("revise_started", task_id, round_no, {})
            before = diff_of(result) if diff_of is not None else None
            prior_calls = list(result.cursor_calls)
            prior_duration = result.duration_s
            prior_scope = list(result.reverted_scope)
            revise_started = time.monotonic()
            prior = result
            try:
                fresh_call = rerun(task_id, result, feedback[:2000], result.attempt + 1)
                if round_budget_s is None:
                    fresh = await fresh_call
                else:
                    fresh = await asyncio.wait_for(fresh_call, timeout=round_budget_s)
            except TimeoutError:
                reason = f"revise round exceeded {round_budget_s:g}s"
                if _holdable(prior):
                    result = _needs_review(prior, prior)
                    verdict = ReviewVerdict(
                        task_id=task_id,  # type: ignore[arg-type]
                        verdict="reject",
                        confidence=1,
                        reasons=[reason],
                        issues=[reason],
                    )
                else:
                    verdict = _forced_reject(task_id, reason)
                _emit(
                    "review_verdict",
                    task_id,
                    round_no,
                    {"verdict": verdict.verdict, "reason": reason, "status": result.status},
                )
                if timings is not None:
                    timings["revise"] = timings.get("revise", 0.0) + (time.monotonic() - revise_started)
                break
            except Exception as exc:
                verdict = _forced_reject(task_id, str(exc))
                if timings is not None:
                    timings["revise"] = timings.get("revise", 0.0) + (time.monotonic() - revise_started)
                break
            if timings is not None:
                timings["revise"] = timings.get("revise", 0.0) + (time.monotonic() - revise_started)
            result = fresh.model_copy(
                update={
                    "cursor_calls": [*prior_calls, *fresh.cursor_calls],
                    "duration_s": prior_duration + fresh.duration_s,
                    "reverted_scope": [*prior_scope, *fresh.reverted_scope],
                }
            )
            if prove is not None:
                snapshot = dict(results)
                snapshot[task_id] = result
                prove(snapshot)
            if diff_of is not None and diff_of(result) == before:
                base = result if _holdable(result) else prior
                if _holdable(base):
                    result = _needs_review(base, result)
                message = (result.cursor_message or "").strip()[:500]
                reasons = ["no new diff", message] if message else ["no new diff"]
                verdict = ReviewVerdict(
                    task_id=task_id,  # type: ignore[arg-type]
                    verdict="reject",
                    confidence=1,
                    reasons=reasons,
                    issues=reasons,
                )
                _emit(
                    "review_verdict",
                    task_id,
                    round_no,
                    {
                        "verdict": "reject",
                        "reason": verdict_reason(verdict),
                        "cursor_message": result.cursor_message,
                        "status": result.status,
                    },
                )
                break
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


def selected_branches(
    plan: Plan,
    results: dict[str, WorkerResult],
    verdicts: dict[str, ReviewVerdict],
    *,
    include_needs_review: bool = False,
) -> tuple[dict[str, str], dict[str, str]]:
    """Approved branches whose upstream was not rejected. A rejected upstream drops its dependents."""
    branches: dict[str, str] = {}
    reasons: dict[str, str] = {}
    blocked: dict[str, str] = {}
    by_id = {task.id: task for task in plan.tasks}
    order = [task_id for wave in plan.waves() for task_id in wave]
    for task_id in order:
        task = by_id[task_id]
        upstream = next((dep for dep in task.depends_on if dep in blocked), None)
        if upstream is None:
            for dep in task.depends_on:
                parent = results.get(dep)
                verdict = verdicts.get(dep)
                if parent is None or parent.status != "done" or (verdict is not None and verdict.verdict != "approve"):
                    upstream = dep
                    break
        if upstream is not None:
            blocked[task_id] = upstream
            parent = results.get(upstream)
            if parent is not None and parent.status == "needs_review":
                reasons[task_id] = f"upstream {upstream} needs review"
            else:
                reasons[task_id] = f"upstream {upstream} rejected"
            continue
        verdict = verdicts.get(task_id)
        result = results.get(task_id)
        if include_needs_review and result is not None and result.status == "needs_review" and result.head_sha:
            branches[task_id] = result.branch
            continue
        if result is not None and result.status == "needs_review":
            why = verdict.reasons[0] if verdict is not None and verdict.reasons else "needs review"
            reasons[task_id] = f"needs review: {why}"[:300]
            continue
        if verdict and verdict.verdict == "approve" and result and result.status == "done" and result.head_sha:
            branches[task_id] = result.branch
            continue
        if verdict is None:
            reasons[task_id] = "not approved"
        elif verdict.verdict == "reject":
            reasons[task_id] = "rejected: " + (verdict.reasons[0] if verdict.reasons else "rejected")
        else:
            reasons[task_id] = verdict.verdict
    return branches, reasons


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

    from treefleet.contracts import ReviewerUsage
    from treefleet.orchestrator.callbacks import UsageBox

    snapshot = gitops_snapshot(bundle.repo, bundle.request.base_ref, result.branch)
    prompt = _prompt(task, result, round_no, snapshot, _jev_note(bundle, task, snapshot))
    box = UsageBox()
    models = [bundle.settings.models.fast, bundle.settings.models.strong]

    async def complete_for(model: str, text: str) -> str:
        return await _complete(
            LiteLlm(model=model),
            text,
            sink=bundle.sink,
            run_id=bundle.run_id,
            usage=box,
            agent_name="reviewer",
            instruction=REVIEW_INSTRUCTION,
            output_key="verdict",
            tool_guard=screen_review_call,
        )

    verdict, used = await review_or_escalate(complete_for, models, result.task_id, prompt)
    reported = box.usage.input_tokens + box.usage.output_tokens > 0
    bundle.reviewer_usages.append(
        ReviewerUsage(
            task_id=result.task_id,
            round=round_no,
            model=used,
            usage=box.usage,
            reported=reported,
        )
    )
    from treefleet.git.proofs import in_scope

    outside = any(
        not in_scope(item.path, list(task.allowed_paths), list(task.denied_paths)) for item in result.files_changed
    )
    names = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", task.instructions)
    return apply_rubric(
        verdict,
        checks_failed=any(not item.passed for item in result.checks),
        outside_scope=outside,
        empty_diff=not result.files_changed,
        test_text=snapshot,
        functions=names,
    )


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

        timings: dict[str, float] = {}
        updated, verdicts = await review_loop(
            [task.id for task in plan.tasks],
            results,
            review=lambda task_id, result, round_no: _review_one(self.bundle, plan, result, round_no),
            rerun=lambda task_id, _result, feedback, attempt: _redispatch(self.bundle, task_id, feedback, attempt),
            prove=prove_now,
            emit=lambda kind, task_id, round_no, payload: _emit(self.bundle, kind, task_id, round_no, payload),
            diff_of=lambda result: gitops_snapshot(self.bundle.repo, self.bundle.request.base_ref, result.branch),
            timings=timings,
            max_revisions=self.bundle.settings.revise.max_rounds,
            round_budget_s=self.bundle.settings.revise.round_budget_s,
        )
        self.bundle.review_seconds += timings.get("review", 0.0)
        self.bundle.revise_seconds += timings.get("revise", 0.0)
        _phase(self.bundle, "review", self.bundle.review_seconds)
        _phase(self.bundle, "revise", self.bundle.revise_seconds)
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
        branches, reasons = selected_branches(
            plan,
            results,
            verdicts,
            include_needs_review=bool(getattr(self.bundle.request, "include_needs_review", False)),
        )
        import time

        started = time.monotonic()
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
        self.bundle.integration_seconds = time.monotonic() - started
        _phase(self.bundle, "integrate", self.bundle.integration_seconds)
        yield _adk_event(ctx, self.name, {StateKey.INTEGRATION: report.model_dump(mode="json")})
