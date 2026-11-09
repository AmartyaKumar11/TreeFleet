"""Reviewer, revise loop, and integrator. The reviewer has no mutating tool."""

from __future__ import annotations

import json
import os
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from treefleet.contracts import CheckResult, Plan, ReviewVerdict, RunEvent, StateKey, WorkerResult
from treefleet.git.worktrees import GitError, git_out
from treefleet.guards.paths import path_violation
from treefleet.guards.text import redact
from treefleet.llm_retry import ModelUnavailableError, with_llm_retry
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
from treefleet.reactions import Action, Decision, Event, ReactionTable, default_table, reaction_payload, resolve

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
    reactions: ReactionTable | None = None,
) -> tuple[dict[str, WorkerResult], dict[str, ReviewVerdict]]:
    """One reviewer per task, in parallel. A revise re-runs the same branch within the round budget."""
    import asyncio

    table = reactions if reactions is not None else default_table(review_rounds=max_revisions)
    bound = table.get(Event.review_revise).retries

    def _fired(event: Event, task_id: str, attempt: int) -> Decision:
        decision = resolve(event, attempt, table)
        if emit is not None:
            emit("reaction.fired", task_id, attempt, reaction_payload(event, decision, task_id))
        return decision

    def _emit(kind: str, task_id: str, round_no: int, payload: dict[str, Any]) -> None:
        if kind == "review_verdict" and payload.get("verdict") == "reject":
            _fired(Event.review_rejected, task_id, 1)
        if emit is not None:
            emit(kind, task_id, round_no, payload)

    async def _one(task_id: str) -> tuple[str, WorkerResult, ReviewVerdict]:
        import time

        result = results[task_id]
        error = result.error or ""
        if result.status == "failed" and not any(not item.passed for item in result.checks):
            _fired(Event.task_failed, task_id, 1)
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
            attempt = 1
            while True:
                decision = _fired(Event.checks_failed, task_id, attempt)
                if decision.action != Action.send_to_agent:
                    break
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
                attempt += 1
        verdict: ReviewVerdict | None = None
        for round_no in range(1, bound + 2):
            _emit("review_started", task_id, round_no, {})
            started = time.monotonic()
            try:
                verdict = await review(task_id, result, round_no)
            except ModelUnavailableError as exc:
                reason = f"reviewer unavailable: {exc.error_class}"
                if result.status in {"done", "needs_review"} and result.commit_made and result.head_sha:
                    _fired(Event.needs_review, task_id, 1)
                    result = result.model_copy(update={"status": "needs_review", "error": reason[:500]})
                    status = "needs_review"
                else:
                    result = result.model_copy(update={"error": reason[:500]})
                    status = result.status
                verdict = ReviewVerdict(
                    task_id=task_id,  # type: ignore[arg-type]
                    verdict="revise",
                    confidence=0,
                    reasons=[reason],
                    issues=[reason],
                    required_changes=["Retry review when the model is available"],
                    revision_instructions="Retry review when the model is available",
                )
                _emit(
                    "review_verdict",
                    task_id,
                    round_no,
                    {"verdict": "revise", "reason": reason, "status": status},
                )
                if timings is not None:
                    timings["review"] = timings.get("review", 0.0) + (time.monotonic() - started)
                break
            except Exception as exc:
                verdict = _forced_reject(task_id, type(exc).__name__)
                _emit("review_verdict", task_id, round_no, {"verdict": "reject", "reason": verdict_reason(verdict)})
                if timings is not None:
                    timings["review"] = timings.get("review", 0.0) + (time.monotonic() - started)
                break
            if timings is not None:
                timings["review"] = timings.get("review", 0.0) + (time.monotonic() - started)
            if (
                verdict.verdict == "revise"
                and _fired(Event.review_revise, task_id, round_no).action != Action.send_to_agent
            ):
                if _holdable(result):
                    _fired(Event.needs_review, task_id, 1)
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
                if verdict.verdict == "approve":
                    _fired(Event.approved_green, task_id, 1)
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
                    _fired(Event.needs_review, task_id, 1)
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
                _fired(Event.review_rejected, task_id, 1)
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
                    _fired(Event.needs_review, task_id, 1)
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
        pause = os.environ.get("TREEFLEET_STUB_PAUSE")
        if pause:
            import asyncio

            await asyncio.sleep(float(pause))
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
    used = models[0]

    def _emit_llm(kind: str, payload: dict[str, Any]) -> None:
        _emit(bundle, kind, result.task_id, round_no, payload)

    async def complete_once(model: str) -> str:
        return await _complete(
            LiteLlm(model=model),
            prompt,
            sink=bundle.sink,
            run_id=bundle.run_id,
            usage=box,
            agent_name="reviewer",
            instruction=REVIEW_INSTRUCTION,
            output_key="verdict",
            tool_guard=screen_review_call,
        )

    async def complete_for(model: str, text: str) -> str:
        nonlocal used
        used = model

        async def _call(chosen: str) -> str:
            nonlocal used
            used = chosen
            return await _complete(
                LiteLlm(model=chosen),
                text,
                sink=bundle.sink,
                run_id=bundle.run_id,
                usage=box,
                agent_name="reviewer",
                instruction=REVIEW_INSTRUCTION,
                output_key="verdict",
                tool_guard=screen_review_call,
            )

        return await with_llm_retry(
            _call,
            models=[model, models[-1]] if model != models[-1] else [model],
            emit=_emit_llm,
            agent="reviewer",
            task_id=result.task_id,
            reactions=bundle.settings.reactions,
        )

    # First attempt through retry helper; parse/repair still uses review_or_escalate on success path.
    try:
        first = await with_llm_retry(
            complete_once,
            models=models,
            emit=_emit_llm,
            agent="reviewer",
            task_id=result.task_id,
            reactions=bundle.settings.reactions,
        )
    except ModelUnavailableError:
        raise
    verdict = verdict_from_text(first, result.task_id)
    if verdict_unparseable(verdict) and len(models) > 1:

        async def complete_strong(text: str) -> str:
            return await complete_for(models[1], text)

        verdict = await verdict_with_repair(complete_strong, result.task_id, prompt)
        used = models[1]
    elif verdict_unparseable(verdict):
        verdict = await verdict_with_repair(lambda text: complete_for(models[0], text), result.task_id, prompt)
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
        from treefleet.sessions import note_stage, note_task

        note_stage(self.bundle, "review")
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
            reactions=self.bundle.settings.reactions,
        )
        for task_id, verdict in verdicts.items():
            note_task(self.bundle, task_id, verdict=verdict.verdict, status=updated[task_id].status)
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


def _pr_push(bundle: RunBundle) -> Callable[[str], str]:
    """Use an injected pusher in tests. Otherwise push the task branch."""
    from treefleet.pr.stage import git_push

    injected = getattr(bundle, "pr_push", None)
    if injected is not None:
        return injected

    def push(task_id: str) -> str:
        return git_push(bundle.repo, bundle.run_id, task_id, bundle.settings.pr)

    return push


async def _run_pr(bundle: RunBundle, ctx: Any) -> dict[str, Any]:
    """Push approved branches and watch their pull requests. Does not build an integrated branch."""
    import asyncio
    import concurrent.futures
    import time

    from treefleet.git.merge import conflict_revision_feedback, merge_tip_into_task
    from treefleet.pr.loop import PollHooks
    from treefleet.pr.stage import drive, git_push
    from treefleet.sessions import note_stage, note_task

    note_stage(bundle, "awaiting_prs")
    plan = read_plan(ctx.session.state)
    results = read_results(ctx.session.state)
    verdicts = {
        item.task_id: item
        for item in (ReviewVerdict.model_validate(raw) for raw in ctx.session.state.get(StateKey.VERDICTS, []))
    }
    branches, _reasons = selected_branches(
        plan,
        results,
        verdicts,
        include_needs_review=bool(getattr(bundle.request, "include_needs_review", False)),
    )
    by_id = {task.id: task for task in plan.tasks}
    rows: list[dict[str, str]] = []
    for task_id in branches:
        task = by_id[task_id]
        verdict = verdicts.get(task_id)
        note = ""
        if verdict is not None and verdict.reasons:
            note = str(verdict.reasons[0])
        rows.append(
            {
                "id": task_id,
                "title": task.title,
                "goal": bundle.request.goal,
                "scope": ", ".join(task.allowed_paths),
                "verdict": note[:180],
                "checks": "pending",
            }
        )

    def emit(kind: str, task_id: str, payload: dict[str, object]) -> None:
        _emit(bundle, kind, task_id, 0, {key: value for key, value in payload.items()})
        fields: dict[str, object] = {}
        if kind == "pr.opened":
            fields = {
                "branch": f"tf/{bundle.run_id}/{task_id}",
                "pr_number": payload.get("number"),
                "pr_url": str(payload.get("url") or ""),
                "pr_head_sha": str(payload.get("sha") or ""),
                "pr_open": True,
                "pr_status": "open",
                "pr_checks": "pending",
            }
        elif kind == "pr.merged":
            fields = {"status": "done", "pr_status": "merged", "pr_open": False, "pr_checks": "passing"}
        elif kind == "pr.closed":
            fields = {
                "status": "cancelled",
                "pr_status": "closed",
                "pr_open": False,
                "reason": str(payload.get("reason") or "pull request closed"),
            }
        elif kind == "pr.checks" and payload.get("status") == "needs_review":
            fields = {"status": "needs_review", "pr_status": "needs_review", "reason": "round limit reached"}
        elif kind == "pr.checks" and payload.get("checks"):
            state_name = "approved_green" if payload.get("checks") == "passing" else "open"
            fields = {"pr_checks": payload.get("checks"), "pr_status": state_name}
        if fields:
            note_task(bundle, task_id, **fields)

    def revise(task_id: str, feedback: str) -> str | None:
        injected = getattr(bundle, "pr_revise", None)
        if injected is not None:
            return injected(task_id, feedback)
        result = results.get(task_id)
        attempt = (result.attempt + 1) if result is not None else 2

        def _run() -> WorkerResult:
            return asyncio.run(_redispatch(bundle, task_id, feedback, attempt))

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            try:
                fresh = pool.submit(_run).result(timeout=bundle.settings.revise.round_budget_s + 60)
            except Exception:
                return None
        if not fresh.commit_made:
            return None
        results[task_id] = fresh
        try:
            return git_push(bundle.repo, bundle.run_id, task_id, bundle.settings.pr)
        except Exception:
            return None

    def resolve_conflict(task_id: str) -> str | None:
        injected = getattr(bundle, "pr_resolve", None)
        if injected is not None:
            return injected(task_id)
        worktree = bundle.worktree_root / bundle.run_id / task_id
        try:
            left = merge_tip_into_task(worktree, bundle.settings.pr.base)
        except Exception:
            return None
        feedback = conflict_revision_feedback(left, bundle.settings.pr.base)
        return revise(task_id, feedback)

    provider = getattr(bundle, "pr_provider", None)
    if provider is None:
        from treefleet.pr.gh import GhProvider

        provider = GhProvider(bundle.settings.pr.repo)
    from treefleet.git.worktrees import run_git

    remote = run_git(["remote", "get-url", "origin"], cwd=bundle.repo)
    remote_url = remote.stdout.strip() if remote.returncode == 0 else ""
    hooks = PollHooks(revise=revise, resolve_conflict=resolve_conflict, emit=emit)
    opened, outcome = drive(
        rows,
        provider,
        hooks,
        bundle.settings.pr,
        run_id=bundle.run_id,
        remote_url=remote_url,
        push=_pr_push(bundle),
        login=str(getattr(bundle, "pr_login", "") or ""),
        hook_hosts=list(getattr(bundle, "pr_hook_hosts", []) or []),
        sleep=getattr(bundle, "pr_sleep", None) or time.sleep,
        now=getattr(bundle, "pr_now", None) or time.monotonic,
        deadline=getattr(bundle, "pr_deadline", None),
    )
    if outcome == "timed_out":
        note_stage(bundle, "timed_out")
    for item in opened:
        note_task(
            bundle,
            item.task_id,
            pr_number=item.number,
            pr_url=item.url,
            pr_head_sha=item.head_sha,
            pr_last_comment_id=item.last_comment_id,
            pr_open=item.status not in {"merged", "cancelled"},
            pr_status=item.status,
            pr_checks=item.checks,
        )
    return {}


class IntegratorAgent(BaseAgent):
    bundle: Any

    async def _run_async_impl(self, ctx: Any) -> AsyncGenerator[Any, None]:
        if not self.bundle.approved:
            return
        import asyncio
        import concurrent.futures
        import time

        from treefleet.git.merge import conflict_revision_feedback, integrate, merge_tip_into_task
        from treefleet.sessions import note_stage

        if getattr(self.bundle.settings, "pr", None) and self.bundle.settings.pr.enabled:
            delta = await _run_pr(self.bundle, ctx)
            yield _adk_event(ctx, self.name, delta)
            return

        note_stage(self.bundle, "integrate")

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

        def resolve_once(task_id: str, tip: str, paths: list[str]) -> bool:
            worktree = self.bundle.worktree_root / self.bundle.run_id / task_id
            if not worktree.is_dir():
                return False
            try:
                merge_tip_into_task(worktree, tip)
            except Exception:
                return False
            feedback = conflict_revision_feedback(paths, tip)
            result = results.get(task_id)
            attempt = (result.attempt + 1) if result is not None else 2
            _emit(self.bundle, "revise_started", task_id, attempt, {"reason": "merge_conflict"})

            def _run() -> WorkerResult:
                return asyncio.run(_redispatch(self.bundle, task_id, feedback, attempt))

            # integrate() is sync; redispatch is async. One worker thread, one attempt.
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                try:
                    fresh = pool.submit(_run).result(timeout=self.bundle.settings.revise.round_budget_s + 60)
                except Exception:
                    return False
            if fresh.status not in {"done", "needs_review"} or not fresh.commit_made:
                return False
            results[task_id] = fresh
            return True

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
            resolve_once=resolve_once,
            reactions=self.bundle.settings.reactions,
            on_reaction=lambda task_id, decision: _emit(
                self.bundle,
                "reaction.fired",
                task_id,
                decision.attempt,
                reaction_payload(Event.merge_conflict, decision, task_id),
            ),
        )
        self.bundle.integration_seconds = time.monotonic() - started
        _phase(self.bundle, "integrate", self.bundle.integration_seconds)
        delta: dict[str, Any] = {StateKey.INTEGRATION: report.model_dump(mode="json")}
        if results:
            delta[StateKey.WORKER_RESULTS] = {key: value.model_dump(mode="json") for key, value in results.items()}
        yield _adk_event(ctx, self.name, delta)
