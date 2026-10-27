"""Cost lines. An unknown rate stays at zero dollars and says so."""

from __future__ import annotations

from datetime import UTC, datetime

from treefleet.config import Pricing, Settings
from treefleet.contracts import CostLine, CursorCall, ReviewerUsage, TokenUsage, WorkerResult


def is_peak(when: datetime) -> bool:
    """DeepSeek peak is Mon-Fri 01:00-04:00 and 06:00-10:00 UTC. Off-peak is half."""
    moment = when if when.tzinfo is not None else when.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    if moment.weekday() >= 5:
        return False
    hour = moment.hour
    return (1 <= hour < 4) or (6 <= hour < 10)


def line_for(
    component: str,
    model: str,
    usage: TokenUsage,
    *,
    rate_in: float | None,
    rate_out: float | None,
    rate_cache: float | None = None,
    estimate: bool = False,
    reported: bool = True,
    task_id: str | None = None,
) -> CostLine:
    if not reported:
        return CostLine(
            component=component,  # type: ignore[arg-type]
            model=model,
            usage=TokenUsage(),
            usd=0.0,
            label="usage not reported",
            task_id=task_id,
        )
    if rate_in is None and rate_out is None:
        usd = 0.0
        label = "rate unknown"
    else:
        cached = usage.cached_input_tokens
        fresh = max(0, usage.input_tokens - cached)
        cache_rate = rate_in if rate_cache is None else rate_cache
        usd = (fresh / 1_000_000) * (rate_in or 0.0)
        usd += (cached / 1_000_000) * (cache_rate or 0.0)
        usd += (usage.output_tokens / 1_000_000) * (rate_out or 0.0)
        label = "estimate" if estimate else ""
    return CostLine(
        component=component,  # type: ignore[arg-type]
        model=model,
        usage=usage,
        usd=round(usd, 6),
        label=label,
        task_id=task_id,
    )


def _deepseek(pricing: Pricing, model: str) -> tuple[float | None, float | None, float | None]:
    name = model.lower()
    if "flash" in name:
        return (
            pricing.deepseek_flash_input_per_million,
            pricing.deepseek_flash_cache_hit_per_million,
            pricing.deepseek_flash_output_per_million,
        )
    if "pro" in name:
        return (
            pricing.deepseek_pro_input_per_million,
            pricing.deepseek_pro_cache_hit_per_million,
            pricing.deepseek_pro_output_per_million,
        )
    return (pricing.deepseek_input_per_million, None, pricing.deepseek_output_per_million)


def _cursor(pricing: Pricing, model: str) -> tuple[float | None, float | None, float | None]:
    name = model.lower()
    if "composer" in name and "fast" in name:
        return (
            pricing.cursor_composer_fast_input_per_million,
            pricing.cursor_composer_fast_cache_read_per_million,
            pricing.cursor_composer_fast_output_per_million,
        )
    if "composer" in name:
        return (
            pricing.cursor_composer_input_per_million,
            pricing.cursor_composer_cache_read_per_million,
            pricing.cursor_composer_output_per_million,
        )
    return (pricing.cursor_input_per_million, None, pricing.cursor_output_per_million)


def _priced(
    component: str,
    model: str,
    usage: TokenUsage,
    rates: tuple[float | None, float | None, float | None],
    *,
    estimate: bool,
    reported: bool,
    peak: bool,
    task_id: str | None = None,
) -> CostLine:
    rate_in, rate_cache, rate_out = rates
    if peak and not estimate:
        rate_in = None if rate_in is None else rate_in * 2
        rate_cache = None if rate_cache is None else rate_cache * 2
        rate_out = None if rate_out is None else rate_out * 2
    item = line_for(
        component,
        model,
        usage,
        rate_in=rate_in,
        rate_out=rate_out,
        rate_cache=rate_cache,
        estimate=estimate,
        reported=reported,
        task_id=task_id,
    )
    if peak and item.label == "" and item.usd > 0:
        return item.model_copy(update={"label": "peak"})
    return item


def usage_from_object(metrics: object) -> TokenUsage | None:
    """Read CrewAI UsageMetrics or a dict. Zeros mean the source had no counts."""
    if metrics is None:
        return None

    def _get(*names: str) -> int:
        for name in names:
            raw = metrics.get(name) if isinstance(metrics, dict) else getattr(metrics, name, None)
            if raw:
                return int(raw)
        return 0

    prompt = _get("prompt_tokens", "input_tokens")
    completion = _get("completion_tokens", "output_tokens")
    cached = _get("cached_prompt_tokens", "prompt_cache_hit_tokens", "cached_tokens")
    if prompt == 0 and completion == 0 and cached == 0:
        return None
    return TokenUsage(input_tokens=prompt, output_tokens=completion, cached_input_tokens=cached)


def _cursor_line(
    pricing: Pricing,
    result: WorkerResult,
    call: CursorCall | None,
    usage: TokenUsage,
    *,
    reported: bool,
    model: str,
) -> CostLine:
    item = _priced(
        "cursor",
        model,
        usage,
        _cursor(pricing, model),
        estimate=True,
        reported=reported,
        peak=False,
        task_id=result.task_id,
    )
    if call is None:
        return item
    if item.label == "usage not reported":
        return item
    note = f"{result.task_id} r{call.round}"
    if item.label == "estimate":
        note = f"{note} estimate"
    return item.model_copy(update={"label": note[:40]})


def cost_rows(
    pricing: Pricing,
    results: list[WorkerResult],
    *,
    planner: TokenUsage,
    planner_model: str,
    worker_model: str,
    jev_calls: int,
    jev_tokens: int,
    when: datetime | None = None,
    reviewer: list[ReviewerUsage] | None = None,
) -> list[CostLine]:
    """Planner, worker, one cursor row per call, reviewer, then Jev."""
    moment = when or datetime.now(UTC)
    peak = is_peak(moment)
    lines = [
        _priced(
            "planner",
            planner_model,
            planner,
            _deepseek(pricing, planner_model),
            estimate=False,
            reported=True,
            peak=peak,
        )
    ]
    if results:
        reported = [item for item in results if item.brain_usage_reported]
        if reported:
            usage = TokenUsage()
            for item in reported:
                usage = usage + item.brain_usage
            model = reported[0].model or worker_model
            lines.append(
                _priced(
                    "worker",
                    model,
                    usage,
                    _deepseek(pricing, model),
                    estimate=False,
                    reported=True,
                    peak=peak,
                )
            )
        else:
            lines.append(line_for("worker", worker_model, TokenUsage(), rate_in=None, rate_out=None, reported=False))
        for result in results:
            model = result.cursor_model or "cursor"
            if result.cursor_calls:
                for call in result.cursor_calls:
                    lines.append(
                        _cursor_line(
                            pricing,
                            result,
                            call,
                            call.usage,
                            reported=call.reported,
                            model=call.model or model,
                        )
                    )
            else:
                lines.append(
                    _cursor_line(
                        pricing,
                        result,
                        None,
                        result.usage,
                        reported=result.cursor_usage_reported,
                        model=model,
                    )
                )
    for item in reviewer or []:
        priced = _priced(
            "reviewer",
            item.model,
            item.usage,
            _deepseek(pricing, item.model),
            estimate=False,
            reported=item.reported,
            peak=peak,
            task_id=item.task_id,
        )
        if priced.label != "usage not reported":
            note = f"{item.task_id} r{item.round}"
            if priced.label:
                note = f"{note} {priced.label}"
            priced = priced.model_copy(update={"label": note[:40]})
        lines.append(priced)
    if jev_calls:
        item = line_for(
            "jev",
            "jev-latest",
            TokenUsage(input_tokens=jev_tokens),
            rate_in=pricing.jev_usd_per_million_input,
            rate_out=0.0,
        )
        lines.append(item.model_copy(update={"label": f"calls: {jev_calls}"}))
    return lines


def collect_costs(
    settings: Settings,
    results: list[WorkerResult],
    *,
    planner: TokenUsage,
    planner_model: str,
    jev_calls: int,
    jev_tokens: int,
    when: datetime | None = None,
    reviewer: list[ReviewerUsage] | None = None,
) -> list[CostLine]:
    return cost_rows(
        settings.pricing,
        results,
        planner=planner,
        planner_model=planner_model,
        worker_model=settings.models.fast,
        jev_calls=jev_calls,
        jev_tokens=jev_tokens,
        when=when,
        reviewer=reviewer,
    )
