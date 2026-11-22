"""One event sink per process: JSONL on disk and in-memory queues for SSE."""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from treefleet.contracts import RunEvent
from treefleet.guards.text import redact_payload

LOCK_DEADLINE_S = 30.0
WRITE_ATTEMPTS = 6
WRITE_BACKOFF_START_S = 0.005
WRITE_BACKOFF_CAP_S = 0.080
OFFSET_READ_MAX_BYTES = 1_000_000


class LockTimeoutError(TimeoutError):
    """Raised when the events.jsonl cross-process lock is not acquired in time."""


class BadCursorError(ValueError):
    """Client offset is not on a JSONL line boundary."""


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


@dataclass(frozen=True)
class OffsetPage:
    """One page of events read by byte offset. Cost does not grow with file length."""

    items: list[dict[str, Any]]
    next_offset: int
    eof: bool
    skipped: int
    reset: bool
    size: int


def _is_line_boundary(handle: Any, offset: int) -> bool:
    if offset == 0:
        return True
    if offset < 0:
        return False
    handle.seek(offset - 1)
    return handle.read(1) == b"\n"


def read_events_offset(
    path: Path,
    offset: int,
    limit: int,
    *,
    max_bytes: int = OFFSET_READ_MAX_BYTES,
    require_boundary: bool = True,
) -> OffsetPage:
    """Read at most `limit` complete JSONL lines starting at byte `offset`.

    A final line without a trailing newline is in-progress and not consumed.
    Invalid JSON lines are skipped and counted. Cost is bounded by max_bytes.
    """
    if limit < 0:
        raise ValueError("limit must be >= 0")
    if offset < 0:
        raise BadCursorError("offset must be >= 0")
    if not path.is_file():
        return OffsetPage(items=[], next_offset=0, eof=True, skipped=0, reset=False, size=0)

    size = path.stat().st_size
    reset = False
    start = offset
    if start > size:
        reset = True
        start = 0

    with path.open("rb") as handle:
        if require_boundary and not _is_line_boundary(handle, start):
            raise BadCursorError(f"offset {start} is not on a line boundary")
        handle.seek(start)
        budget = max(1, max_bytes)
        chunk = handle.read(budget)
        # If we filled the budget mid-file, keep only complete lines.
        if len(chunk) == budget and handle.tell() < size:
            cut = chunk.rfind(b"\n")
            if cut < 0:
                return OffsetPage(items=[], next_offset=start, eof=False, skipped=0, reset=reset, size=size)
            chunk = chunk[: cut + 1]

    items: list[dict[str, Any]] = []
    skipped = 0
    pos = start
    consumed = 0
    while consumed < len(chunk) and len(items) < limit:
        nl = chunk.find(b"\n", consumed)
        if nl < 0:
            break
        raw = chunk[consumed:nl]
        line_start = pos
        line_bytes = nl - consumed + 1
        consumed = nl + 1
        pos += line_bytes
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            continue
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            skipped += 1
            continue
        if not isinstance(parsed, dict):
            skipped += 1
            continue
        row = dict(parsed)
        row["offset"] = line_start
        items.append(row)

    next_offset = pos
    # Incomplete trailing bytes (no final newline) stay unconsumed → next_offset < size.
    eof = next_offset >= size
    return OffsetPage(
        items=items,
        next_offset=next_offset,
        eof=eof,
        skipped=skipped,
        reset=reset,
        size=size,
    )


def _lock(fd: int, *, deadline_s: float = LOCK_DEADLINE_S) -> None:
    """Acquire the lock with a hard deadline. Uses non-blocking primitives so the wait is ours."""
    deadline = time.monotonic() + max(0.0, deadline_s)
    if sys.platform == "win32":
        import msvcrt

        # LK_LOCK can block up to ~10s inside the call; LK_NBLCK returns immediately.
        while True:
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise LockTimeoutError(f"events lock not acquired within {deadline_s:.3f}s") from None
                time.sleep(0.001)
    else:
        import fcntl

        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockTimeoutError(f"events lock not acquired within {deadline_s:.3f}s") from None
                time.sleep(0.001)


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


def _write_backoff(attempt: int) -> float:
    base = min(WRITE_BACKOFF_CAP_S, WRITE_BACKOFF_START_S * (2 ** max(0, attempt - 1)))
    return base + random.uniform(0.0, base * 0.25)


def _open_append(path: Path) -> int:
    return os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)


def _open_lock(path: Path) -> int:
    return os.open(str(path), os.O_RDWR | os.O_CREAT)


def _write_all(fd: int, blob: bytes) -> None:
    view = memoryview(blob)
    while view:
        wrote = os.write(fd, view)
        if wrote <= 0:
            raise OSError("event append wrote nothing")
        view = view[wrote:]


def append_lines(path: Path, lines: list[str], *, lock_deadline_s: float = LOCK_DEADLINE_S) -> None:
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
    last_error: BaseException | None = None
    for attempt in range(1, WRITE_ATTEMPTS + 1):
        event_fd: int | None = None
        lock_fd: int | None = None
        try:
            try:
                event_fd = _open_append(path)
                lock_fd = _open_lock(lock_path)
            except OSError as exc:
                last_error = exc
                if attempt >= WRITE_ATTEMPTS:
                    break
                time.sleep(_write_backoff(attempt))
                continue
            try:
                if os.lseek(lock_fd, 0, os.SEEK_END) == 0:
                    os.write(lock_fd, b"\0")
                _lock(lock_fd, deadline_s=lock_deadline_s)
                try:
                    for blob in blobs:
                        _write_all(event_fd, blob)
                finally:
                    _unlock(lock_fd)
            except LockTimeoutError:
                raise
            except OSError as exc:
                last_error = exc
                if attempt >= WRITE_ATTEMPTS:
                    break
                time.sleep(_write_backoff(attempt))
                continue
            return
        finally:
            if event_fd is not None:
                os.close(event_fd)
            if lock_fd is not None:
                os.close(lock_fd)
    assert last_error is not None
    raise OSError(f"event append failed after {WRITE_ATTEMPTS} attempts for {path}") from last_error


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
            try:
                append_lines(path, [item.model_dump_json() for item in pending])
            except LockTimeoutError as exc:
                # Keep the in-memory event; log so a stuck lock does not crash the process.
                print(f"treefleet: events lock timeout writing {path}: {exc}", file=sys.stderr, flush=True)
            stored = pending[0]
            queues = list(self._queues.get(event.run_id, []))
        return stored, queues
