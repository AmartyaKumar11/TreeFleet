"""Cost lines. An unknown rate stays at zero dollars and says so."""

from __future__ import annotations

from treefleet.config import Settings
from treefleet.contracts import CostLine, TokenUsage, WorkerResult


def line_for(
    component: str,
    model: str,
    usage: TokenUsage,
    *,
    rate_in: float | None,
    rate_out: float | None,
    estimate: bool = False,
    task_id: str | None = None,
) -> CostLine:
    if rate_in is None and rate_out is None:
        usd = 0.0
        label = "rate unknown"
    else:
        usd = (usage.input_tokens / 1_000_000) * (rate_in or 0.0)
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


def collect_costs(
    settings: Settings,
    results: list[WorkerResult],
    *,
    planner: TokenUsage,
    planner_model: str,
    jev_calls: int,
    jev_tokens: int,
) -> list[CostLine]:
    pricing = settings.pricing
    lines = [
        line_for(
            "planner",
            planner_model,
            planner,
            rate_in=pricing.deepseek_input_per_million,
            rate_out=pricing.deepseek_output_per_million,
        )
    ]
    for result in results:
        lines.append(
            line_for(
                "cursor",
                result.model,
                result.usage,
                rate_in=pricing.cursor_input_per_million,
                rate_out=pricing.cursor_output_per_million,
                estimate=pricing.cursor_input_per_million is not None,
                task_id=result.task_id,
            )
        )
    if jev_calls:
        usage = TokenUsage(input_tokens=jev_tokens)
        item = line_for(
            "jev",
            "jev-latest",
            usage,
            rate_in=pricing.jev_usd_per_million_input,
            rate_out=0.0,
        )
        lines.append(item.model_copy(update={"label": f"calls: {jev_calls}"}))
    return lines
