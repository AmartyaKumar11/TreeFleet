"""S1: ADK LiteLlm on DeepSeek calls a tool and honors output_schema. Never prints secrets."""

from __future__ import annotations

import asyncio
import os
from importlib.metadata import version
from pathlib import Path

import httpx
from pydantic import BaseModel, ValidationError

CALLED: list[tuple[int, int]] = []


def add(a: int, b: int) -> int:
    """Add two integers and return the sum."""
    CALLED.append((int(a), int(b)))
    return int(a) + int(b)


class Sum(BaseModel):
    total: int


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


def model_ids() -> list[str]:
    response = httpx.get(
        "https://api.deepseek.com/models",
        headers={"Authorization": f"Bearer {os.environ['DEEPSEEK_API_KEY']}"},
        timeout=30,
    )
    response.raise_for_status()
    return [item["id"] for item in response.json()["data"]]


async def run_agent(model: str, schema: type[BaseModel] | None) -> str:
    from google.adk.agents import LlmAgent
    from google.adk.models.lite_llm import LiteLlm
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    CALLED.clear()
    agent = LlmAgent(
        name="adder",
        model=LiteLlm(model=f"deepseek/{model}"),
        instruction="Call the add tool with the two integers in the user message. Reply with JSON {\"total\": <sum>} and nothing else.",
        tools=[add],
        output_schema=schema,
    )
    service = InMemorySessionService()
    await service.create_session(app_name="s1", user_id="u", session_id="s")
    runner = Runner(app_name="s1", agent=agent, session_service=service)
    message = types.Content(role="user", parts=[types.Part(text="Add 2 and 3.")])
    text = ""
    async for event in runner.run_async(user_id="u", session_id="s", new_message=message):
        content = getattr(event, "content", None)
        if content is None:
            continue
        for part in content.parts or []:
            if part.text:
                text = part.text
    return text


def schema_ok(text: str) -> bool:
    try:
        return Sum.model_validate_json(text[text.find("{") : text.rfind("}") + 1]).total == 5
    except (ValidationError, ValueError):
        return False


async def main() -> None:
    load_env()
    print(f"google-adk {version('google-adk')}")
    print(f"litellm {version('litellm')}")
    ids = model_ids()
    print("deepseek_models", ",".join(ids))
    model = "deepseek-flash" if "deepseek-flash" in ids else ids[0]
    schema_pass = False
    try:
        text = await run_agent(model, Sum)
        schema_pass = CALLED == [(2, 3)] and schema_ok(text)
        print(f"with_schema tool_called={CALLED == [(2, 3)]} schema_ok={schema_ok(text)}")
    except Exception as exc:
        print(redact(f"with_schema error={type(exc).__name__}: {exc}"))
    if schema_pass:
        print("Result: PASS")
        return
    try:
        text = await run_agent(model, None)
    except Exception as exc:
        print(redact(f"no_schema error={type(exc).__name__}: {exc}"))
        print("Result: FAIL")
        return
    print(f"no_schema tool_called={CALLED == [(2, 3)]} parsed={schema_ok(text)}")
    if CALLED == [(2, 3)] and schema_ok(text):
        print("Result: FALLBACK drop output_schema, parse JSON with Pydantic and retry with the validation error")
        return
    print("Result: FAIL")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        os._exit(0)
