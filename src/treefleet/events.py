"""One event sink per process: JSONL on disk and in-memory queues for SSE."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from treefleet.contracts import RunEvent
from treefleet.guards.text import redact_payload


def skip_message(count: int) -> str:
    return f"skipped {count} corrupt event lines"


_ARGUMENT = (
    "validation error",
    "field required",
    "error executing tool",
    "bad_args",
    "malformed tool result",
)


def argument_failure(payload: dict[str, Any] | None) -> bool:
    """True for a tool-argument or validation failure, including an old tool_result denial."""
    if not isinstance(payload, dict):
        return False
    if str(payload.get("stage") or "") == "tool_result":
        return True
    reasons = payload.get("reasons")
    if reasons is None:
        reasons = payload.get("message") or ""
    text = " ".join(str(item) for item in reasons) if isinstance(reasons, list) else str(reasons)
    lowered = text.lower()
    return any(marker in lowered for marker in _ARGUMENT)


def read_event_lines(lines: list[str]) -> tuple[list[dict[str, Any]], int]:
    """Parse JSONL. A line that is not a JSON object is counted and dropped."""
    rows: list[dict[str, Any]] = []
    skipped = 0
    for raw in lines:
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if isinstance(item, dict):
            rows.append(item)
        else:
            skipped += 1
    return rows, skipped


def read_events(path: Path) -> tuple[list[dict[str, Any]], int]:
    if not path.is_file():
        return [], 0
    return read_event_lines(path.read_text(encoding="utf-8").splitlines())


def _lock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                return
            except OSError:
                time.sleep(0.001)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


def append_lines(path: Path, lines: list[str]) -> None:
    """One O_APPEND write per line, under a cross-process lock.

    Workers are separate processes, so the in-process thread lock cannot stop two of them
    tearing a line in half. A lock file needs no extra dependency and keeps the single
    events.jsonl every reader already opens.
    """
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    blobs = [(line if line.endswith("\n") else line + "\n").encode("utf-8") for line in lines]
    lock_path = path.with_name(path.name + ".lock")
    event_fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT)
    try:
        if os.lseek(lock_fd, 0, os.SEEK_END) == 0:
            os.write(lock_fd, b"\0")
        _lock(lock_fd)
        try:
            for blob in blobs:
                view = memoryview(blob)
                while view:
                    wrote = os.write(event_fd, view)
                    if wrote <= 0:
                        raise OSError("event append wrote nothing")
                    view = view[wrote:]
        finally:
            _unlock(lock_fd)
    finally:
        os.close(event_fd)
        os.close(lock_fd)


class EventSink:
    def __init__(self, runs_root: Path) -> None:
        self._root = runs_root
        self._lock = threading.Lock()
        self._seq: dict[str, int] = {}
        self._history: dict[str, list[RunEvent]] = {}
        self._queues: dict[str, list[asyncio.Queue[RunEvent]]] = {}

    def prime(self, run_id: str) -> None:
        """Continue sequence numbers when a second process appends to an existing log."""
        path = self._root / run_id / "events.jsonl"
        if not path.is_file():
            return
        last = -1
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    seq = json.loads(text).get("seq")
                except json.JSONDecodeError:
                    continue
                if isinstance(seq, int) and seq > last:
                    last = seq
        self._seq[run_id] = last + 1

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
            append_lines(path, [item.model_dump_json() for item in pending])
            stored = pending[0]
            queues = list(self._queues.get(event.run_id, []))
        return stored, queues
