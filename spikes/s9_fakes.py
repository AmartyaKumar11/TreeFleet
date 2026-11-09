"""S9: scripted tool calls for ADK and CrewAI, plus a fake cursor-agent shim."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path

CALLED: list[tuple[int, int]] = []


def add(a: int, b: int) -> int:
    """Add two integers."""
    CALLED.append((int(a), int(b)))
    return int(a) + int(b)


def shim() -> None:
    import json
    import time

    delay = os.environ.get("TREEFLEET_FAKE_SLEEP")
    if delay:
        time.sleep(float(delay))
    Path("marker.txt").write_text("shim\n", encoding="utf-8")
    edit = os.environ.get("TREEFLEET_FAKE_EDIT", "")
    if edit == "readme":
        note = os.environ.get("TREEFLEET_FAKE_NOTE", "")
        Path("README.md").write_text(f"shim readme\n{note}", encoding="utf-8")
    if edit == "both":
        tests = Path("tests")
        tests.mkdir(exist_ok=True)
        target = tests / "test_admin.py"
        current = target.read_text(encoding="utf-8") if target.is_file() else ""
        target.write_text(
            current + '\n\ndef test_added():\n    """Documented."""\n    assert True\n',
            encoding="utf-8",
        )
        Path("README.md").write_text("injected\n", encoding="utf-8")
    if edit == "outside":
        Path("sneak.txt").write_text("nope\n", encoding="utf-8")
        outside = os.environ.get("TREEFLEET_FAKE_OUTSIDE")
        if outside:
            Path(outside).write_text("escaped\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "shim edit"}]},
                "timestamp_ms": 1,
            }
        ),
        flush=True,
    )
    print(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "result": "shim edit",
                "usage": {"inputTokens": 3, "outputTokens": 5, "cacheReadTokens": 1, "cacheWriteTokens": 0},
            }
        ),
        flush=True,
    )


def crew() -> None:
    from crewai import Agent, Crew, Task
    from crewai.llms.base_llm import BaseLLM
    from crewai.tools import tool

    @tool("add")
    def add_tool(a: int, b: int) -> int:
        """Add two integers."""
        return add(a, b)

    class Scripted(BaseLLM):
        turns: list[int] = []

        def call(self, messages, tools=None, callbacks=None, available_functions=None, **kwargs):  # noqa: ANN001
            self.turns.append(1)
            if len(self.turns) == 1:
                return [{"name": "add", "arguments": {"a": 2, "b": 3}}]
            return "5"

        async def acall(self, messages, tools=None, callbacks=None, available_functions=None, **kwargs):  # noqa: ANN001
            return self.call(messages, tools, callbacks, available_functions, **kwargs)

    agent = Agent(
        role="adder",
        goal="Add numbers.",
        backstory="Uses the tool.",
        llm=Scripted(model="scripted"),
        tools=[add_tool],
        verbose=False,
        max_iter=2,
    )
    task = Task(description="Add 2 and 3 with the add tool.", expected_output="5", agent=agent)
    Crew(agents=[agent], tasks=[task], verbose=False).kickoff()
    print(f"crewai {version('crewai')}")
    print(f"crew_tool_called={CALLED == [(2, 3)]}")
    os._exit(0)


async def adk() -> bool:
    from google.adk.agents import LlmAgent
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    class Scripted(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):  # noqa: ANN001
            pending = True
            for content in llm_request.contents:
                for part in content.parts or []:
                    if part.function_response is not None:
                        pending = False
            if pending:
                call = types.FunctionCall(name="add", args={"a": 2, "b": 3})
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(function_call=call)]))
            else:
                yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="5")]))

    CALLED.clear()
    agent = LlmAgent(name="adder", model=Scripted(model="scripted"), instruction="Use add.", tools=[add])
    service = InMemorySessionService()
    await service.create_session(app_name="s9", user_id="u", session_id="s")
    runner = Runner(app_name="s9", agent=agent, session_service=service)
    message = types.Content(role="user", parts=[types.Part(text="Add 2 and 3.")])
    async for _event in runner.run_async(user_id="u", session_id="s", new_message=message):
        pass
    print(f"google-adk {version('google-adk')}")
    print(f"adk_tool_called={CALLED == [(2, 3)]}")
    return CALLED == [(2, 3)]


def list_models() -> None:
    print("gpt-5")
    print("sonnet-4-thinking")


def main() -> None:
    if "--list-models" in sys.argv:
        list_models()
        return
    if "--shim" in sys.argv:
        shim()
        return
    if "--crew" in sys.argv:
        crew()
        return
    adk_ok = asyncio.run(adk())
    worker = Path(".venv-worker/Scripts/python.exe")
    crew_run = subprocess.run(
        [str(worker), "-u", __file__, "--crew"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    print((crew_run.stdout or "").strip())
    if crew_run.returncode != 0:
        print((crew_run.stderr or "")[-500:])
    folder = Path(tempfile.mkdtemp(prefix="tfs9-"))
    shim_run = subprocess.run([sys.executable, __file__, "--shim"], cwd=folder, shell=False, check=False)
    shim_ok = shim_run.returncode == 0 and (folder / "marker.txt").read_text(encoding="utf-8") == "shim\n"
    print(f"shim_ok={shim_ok}")
    crew_ok = "crew_tool_called=True" in crew_run.stdout
    if adk_ok and crew_ok and shim_ok:
        print("Result: PASS")
    else:
        print(
            "Result: FALLBACK LiteLLM mock_response or a recorded-fixture proxy; the shim is a small Python script on PATH"
        )
    os._exit(0)


if __name__ == "__main__":
    main()
