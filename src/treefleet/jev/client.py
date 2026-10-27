"""One batched Jev call. Retry 429 and 529. Cache identical bodies. Never log the state or the key."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

_RETRY = {429, 529}
_ATTEMPTS = 3
_WAIT_CAP = 30.0


class JevError(RuntimeError):
    """The call failed. The message has no key and no state text."""


class JevAnswer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    noul: float | None = None
    choice: str | None = None
    score: float | None = None
    legend: str | None = None
    confidence: float | None = None


class JevUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    input_tokens: int = 0
    output_tokens: int = 0


class JevResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    answers: dict[str, JevAnswer]
    usage: JevUsage | None = None


class JevClient:
    def __init__(
        self,
        key: str,
        url: str,
        *,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._key = key
        self._url = url
        self._timeout = timeout
        self._transport = transport
        self._cache: dict[str, JevResponse] = {}
        self.calls = 0
        self.input_tokens = 0

    def ask(self, state: str, questions: dict[str, Any]) -> JevResponse:
        body = json.dumps(
            {"state": state, "model": "jev-latest", "questions": questions},
            sort_keys=True,
        )
        cached = self._cache.get(body)
        if cached is not None:
            return cached
        parsed = self._exchange(body)
        self._cache[body] = parsed
        if parsed.usage is not None:
            self.input_tokens += parsed.usage.input_tokens
        return parsed

    def probabilities(self, state: str, questions: dict[str, str]) -> dict[str, float]:
        payload = {name: {"type": "noul", "instructions": text} for name, text in questions.items()}
        parsed = self.ask(state[:4000], payload)
        found: dict[str, float] = {}
        for name, answer in parsed.answers.items():
            if answer.noul is None:
                raise JevError("missing noul")
            found[name] = max(0.0, min(1.0, float(answer.noul)))
        return found

    def complexities(self, tasks: dict[str, str]) -> dict[str, str]:
        questions = {
            task_id: {
                "type": "choice",
                "instructions": "How hard is this coding task to implement?",
                "criteria": {
                    "trivial": "A one-line or single-file edit",
                    "moderate": "A normal change in a few files",
                    "complex": "A cross-cutting change or a risky refactor",
                },
            }
            for task_id in tasks
        }
        state = "\n".join(f"{key}: {value[:400]}" for key, value in tasks.items())
        parsed = self.ask(state[:4000], questions)
        found: dict[str, str] = {}
        for task_id, answer in parsed.answers.items():
            if answer.confidence is not None and answer.confidence < 0.5:
                continue
            legend = (answer.choice or answer.legend or "").strip().lower()
            if legend in {"trivial", "moderate", "complex"}:
                found[task_id] = legend
        return found

    def _exchange(self, body: str) -> JevResponse:
        last = "unavailable"
        for attempt in range(_ATTEMPTS):
            try:
                response = self._send(body)
            except httpx.TimeoutException as exc:
                raise JevError("timeout") from exc
            except httpx.HTTPError as exc:
                raise JevError("unreachable") from exc
            self.calls += 1
            if response.status_code in _RETRY and attempt + 1 < _ATTEMPTS:
                time.sleep(min(_wait(response), _WAIT_CAP))
                last = f"HTTP {response.status_code}"
                continue
            if response.status_code != 200:
                raise JevError(f"HTTP {response.status_code}")
            return self._parse(response)
        raise JevError(last)

    def _send(self, body: str) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
            return client.post(self._url, content=body.encode("utf-8"), headers=headers)

    def _parse(self, response: httpx.Response) -> JevResponse:
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise JevError("malformed response") from exc
        try:
            return JevResponse.model_validate(payload)
        except ValidationError as exc:
            raise JevError("malformed response") from exc


def _wait(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After", "1")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 1.0
