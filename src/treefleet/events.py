"""One event sink per process: JSONL on disk and in-memory queues for SSE."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from treefleet.contracts import RunEvent
from treefleet.guards.text import redact_payload


class EventSink:
    def __init__(self, runs_root: Path) -> None:
        self._root = runs_root
        self._lock = threading.Lock()
        self._seq: dict[str, int] = {}
        self._history: dict[str, list[RunEvent]] = {}
        self._queues: dict[str, list[asyncio.Queue[RunEvent]]] = {}

    async def emit(self, event: RunEvent) -> RunEvent:
        stored, queues = self._record(event)
        for queue in queues:
            queue.put_nowait(stored)
        return stored

    def emit_sync(self, event: RunEvent) -> RunEvent:
        """CrewAI callbacks are synchronous and may run on a worker thread."""
        stored, queues = self._record(event)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running() and threading.current_thread() is not threading.main_thread():
            for queue in queues:
                loop.call_soon_threadsafe(queue.put_nowait, stored)
            return stored
        for queue in queues:
            queue.put_nowait(stored)
        return stored

    async def subscribe(self, run_id: str, last_event_id: int | None = None) -> asyncio.Queue[RunEvent]:
        queue: asyncio.Queue[RunEvent] = asyncio.Queue()
        with self._lock:
            missed = [
                item for item in self._history.get(run_id, []) if last_event_id is None or item.seq > last_event_id
            ]
            self._queues.setdefault(run_id, []).append(queue)
        for item in missed:
            queue.put_nowait(item)
        return queue

    def _record(self, event: RunEvent) -> tuple[RunEvent, list[asyncio.Queue[RunEvent]]]:
        with self._lock:
            seq = self._seq.get(event.run_id, 0)
            clean, count = redact_payload(event.payload)
            stored = event.model_copy(update={"seq": seq, "payload": clean})
            self._seq[event.run_id] = seq + 1
            pending = [stored]
            if count and event.type != "guard_redacted":
                pending.append(
                    stored.model_copy(
                        update={
                            "seq": seq + 1,
                            "type": "guard_redacted",
                            "source": "guard",
                            "payload": {"count": count, "stage": "final_output"},
                        }
                    )
                )
                self._seq[event.run_id] = seq + 2
            self._history.setdefault(event.run_id, []).extend(pending)
            path = self._root / event.run_id / "events.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                for item in pending:
                    handle.write(item.model_dump_json() + "\n")
            stored = pending[0]
            queues = list(self._queues.get(event.run_id, []))
        return stored, queues
