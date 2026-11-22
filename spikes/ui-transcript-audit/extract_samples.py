"""Throwaway: redact and trim samples from a finished run's events.jsonl.

Usage (from repo root):
  .venv-orch/Scripts/python spikes/ui-transcript-audit/extract_samples.py runs/aa68e041

Writes under spikes/ui-transcript-audit/. Never prints secrets; scrub patterns match
guards/text.py plus common GitHub token prefixes.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

_SECRET = re.compile(
    r"sk-[A-Za-z0-9]{8,}"
    r"|apikey_[A-Za-z0-9]{8,}"
    r"|Bearer\s+[A-Za-z0-9._\-]{8,}"
    r"|ghp_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
    re.IGNORECASE,
)


def scrub(value: object, depth: int = 0) -> object:
    if isinstance(value, str):
        text = _SECRET.sub("[redacted]", value)
        return text if len(text) <= 180 else text[:180] + "…"
    if isinstance(value, dict):
        return {key: scrub(item, depth + 1) for key, item in list(value.items())[:20]}
    if isinstance(value, list):
        return [scrub(item, depth + 1) for item in value[:10]]
    return value


def main() -> int:
    run = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/aa68e041")
    out = Path(__file__).resolve().parent
    events_path = run / "events.jsonl"
    events: list[dict[object, object]] = []
    for raw in events_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            events.append(row)

    seen: set[object] = set()
    one_each: list[object] = []
    for event in events:
        kind = event.get("type")
        if kind in seen:
            continue
        seen.add(kind)
        one_each.append(
            scrub(
                {
                    "seq": event.get("seq"),
                    "source": event.get("source"),
                    "type": kind,
                    "task_id": event.get("task_id"),
                    "payload": event.get("payload"),
                }
            )
        )
    (out / "event-types-one-each.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in one_each) + "\n",
        encoding="utf-8",
    )

    head = []
    for event in events[:20]:
        head.append(
            json.dumps(
                scrub(
                    {
                        "seq": event.get("seq"),
                        "source": event.get("source"),
                        "type": event.get("type"),
                        "task_id": event.get("task_id"),
                        "payload": event.get("payload"),
                    }
                ),
                ensure_ascii=False,
            )
        )
    (out / "events-head-20.jsonl").write_text("\n".join(head) + "\n", encoding="utf-8")

    cursor: list[str] = []
    for event in events:
        if event.get("type") not in {"cursor.tool_start", "cursor.tool_finish", "output_line", "worker.step"}:
            continue
        if len(cursor) >= 20:
            break
        cursor.append(
            json.dumps(
                scrub(
                    {
                        "seq": event.get("seq"),
                        "type": event.get("type"),
                        "task_id": event.get("task_id"),
                        "payload": event.get("payload"),
                    }
                ),
                ensure_ascii=False,
            )
        )
    (out / "cursor-and-output-20.jsonl").write_text("\n".join(cursor) + "\n", encoding="utf-8")

    seqs = [event.get("seq") for event in events]
    counts = Counter(seqs)
    meta = {
        "run": str(run),
        "lines": len(events),
        "bytes": events_path.stat().st_size,
        "seq0_count": counts.get(0, 0),
        "unique_seq": len(counts),
        "types": dict(Counter(str(event.get("type")) for event in events).most_common()),
    }
    (out / "stats.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} from {run} ({len(events)} events)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
