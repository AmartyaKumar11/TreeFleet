"""Throwaway: measure offset-page and SSE-tick cost at 5k and 50k events.

Usage (repo root):
  .venv-orch/Scripts/python spikes/ui-transcript-d5a-bench.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from treefleet.events import read_events_offset

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "spikes" / "ui-transcript-d5a-bench"


def _write_log(path: Path, n: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    kinds = ("phase", "output_line", "cursor.tool_start", "worker.step", "review_verdict")
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for index in range(n):
            kind = kinds[index % len(kinds)]
            payload: dict[str, object]
            if kind == "output_line":
                payload = {"line": f"line-{index}"}
            elif kind == "cursor.tool_start":
                payload = {"tool": "read", "path": f"f{index % 50}.py", "ok": None, "detail": ""}
            elif kind == "worker.step":
                payload = {"step": "cursor", "duration_s": 0.1, "timed_out": False, "name": "c", "output_tail": ""}
            elif kind == "review_verdict":
                payload = {"round": 1, "verdict": "approve", "reason": "ok"}
            else:
                payload = {"name": "fleet", "duration_s": 0.01}
            row = {
                "run_id": "bench0001",
                "seq": 0 if index % 3 == 0 else index,
                "ts": "2026-11-21T00:00:00Z",
                "source": "worker" if index % 3 == 0 else "orchestrator",
                "type": kind,
                "task_id": "alpha",
                "payload": payload,
            }
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def _time_page(path: Path, after: int, limit: int = 200) -> tuple[float, int]:
    started = time.perf_counter()
    page = read_events_offset(path, after, limit)
    elapsed = time.perf_counter() - started
    return elapsed, page.next_offset


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for count in (5_000, 50_000):
        path = OUT / f"events-{count}.jsonl"
        if not path.is_file() or sum(1 for _ in path.open(encoding="utf-8")) != count:
            _write_log(path, count)
        size = path.stat().st_size
        first_s, next_off = _time_page(path, 0)
        # Tail: start near end (last ~200 lines). Approximate by size - 40_000 bytes.
        tail_after = max(0, size - 40_000)
        # Snap to a line boundary.
        with path.open("rb") as handle:
            if tail_after:
                handle.seek(tail_after - 1)
                handle.read(1)
                while True:
                    byte = handle.read(1)
                    if not byte or byte == b"\n":
                        break
                tail_after = handle.tell()
        tail_s, _ = _time_page(path, tail_after)
        # SSE tick ≈ one offset page of up to 200 lines from current end cursor.
        tick_s, _ = _time_page(path, size if False else path.stat().st_size)
        # At EOF, page is empty and cheap; measure a tick that still has work: re-read last page.
        tick_s, _ = _time_page(path, tail_after, limit=200)
        rows.append(
            {
                "events": count,
                "bytes": size,
                "first_page_s": round(first_s, 6),
                "tail_page_s": round(tail_s, 6),
                "sse_tick_s": round(tick_s, 6),
                "first_next": next_off,
            }
        )
        print(rows[-1], flush=True)
    (OUT / "results.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
