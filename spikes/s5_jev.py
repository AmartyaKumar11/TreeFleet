"""S5: Jev noul, choice, and score. Never prints the API key."""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

URL = "https://api.typesafe.ai/v1/systemone"


def load_env() -> None:
    for line in Path(".env").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def redact(text: str) -> str:
    for key in ("DEEPSEEK_API_KEY", "TYPESAFE_API_KEY"):
        secret = os.environ.get(key)
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def main() -> None:
    load_env()
    body = {
        "state": "Add a README section to the repository.",
        "model": "jev-latest",
        "questions": {
            "on_topic": {"type": "noul", "instructions": "Is this a request to change software?"},
            "kind": {
                "type": "choice",
                "instructions": "What kind of change is this?",
                "criteria": {"docs": "Documentation only.", "code": "Source code.", "other": "Anything else."},
            },
            "complexity": {
                "type": "score",
                "instructions": "How hard is this task?",
                "criteria": ["trivial", "moderate", "complex"],
            },
        },
    }
    headers = {"Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}"}
    started = time.perf_counter()
    first = httpx.post(URL, json=body, headers=headers, timeout=60)
    first_s = time.perf_counter() - started
    started = time.perf_counter()
    second = httpx.post(URL, json=body, headers=headers, timeout=60)
    second_s = time.perf_counter() - started
    print(f"status={first.status_code} then={second.status_code} latency_s={first_s:.2f},{second_s:.2f}")
    cache = [first.headers.get("cf-cache-status"), second.headers.get("cf-cache-status")]
    print(f"cf_cache={cache}")
    if first.status_code != 200:
        print(redact(first.text[:500]))
        print("Result: FALLBACK heuristic fallbacks only, Jev removed from guards")
        return
    answers = first.json()["answers"]
    shapes = {name: sorted(value) if isinstance(value, dict) else type(value).__name__ for name, value in answers.items()}
    print(f"answers={shapes}")
    ok = {"noul", "choice", "score"} <= {key for value in shapes.values() if isinstance(value, list) for key in value}
    print("Result: PASS" if ok else "Result: FAIL")


if __name__ == "__main__":
    main()
