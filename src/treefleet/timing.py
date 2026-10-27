"""In-memory step timings plus worker.step events. No agent framework imports."""

from __future__ import annotations

from treefleet.contracts import RunEvent, StepTiming
from treefleet.events import EventSink

steps: list[StepTiming] = []


def reset_steps() -> None:
    steps.clear()


def record_step(
    step: str,
    duration_s: float,
    *,
    timed_out: bool = False,
    name: str = "",
    output_tail: str = "",
    sink: EventSink | None = None,
    run_id: str | None = None,
) -> StepTiming:
    item = StepTiming(
        step=step,  # type: ignore[arg-type]
        duration_s=round(duration_s, 3),
        timed_out=timed_out,
        name=name[:100],
        output_tail=output_tail[:2000],
    )
    steps.append(item)
    if sink is not None and run_id:
        sink.emit_sync(RunEvent(run_id=run_id, seq=0, source="worker", type="worker.step", payload=item.model_dump()))
    return item
