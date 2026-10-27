"""Worker events. Cursor output lines are redacted before they are stored."""

from __future__ import annotations

from treefleet.contracts import OutputLine, RunEvent
from treefleet.cursor_run import redact_line
from treefleet.events import EventSink


def emit_output_line(sink: EventSink, run_id: str, text: str) -> RunEvent:
    line = OutputLine(line=redact_line(text))
    return sink.emit_sync(
        RunEvent(run_id=run_id, seq=0, source="worker", type="output_line", payload=line.model_dump())
    )
