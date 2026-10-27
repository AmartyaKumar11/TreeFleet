"""S4: ADK RemoteA2aAgent sends a DataPart and receives one back, twice in parallel."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from importlib.metadata import version

PORT = 9101
CARD = f"http://127.0.0.1:{PORT}/.well-known/agent-card.json"
TAG_S = b"<a2a_datapart_json>"
TAG_E = b"</a2a_datapart_json>"


def serve() -> None:
    import uvicorn
    from a2a.server.agent_execution import AgentExecutor, RequestContext
    from a2a.server.apps import A2AStarletteApplication
    from a2a.server.events import EventQueue
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
    from a2a.types import AgentCapabilities, AgentCard, AgentSkill, DataPart, Part, TaskState

    class Echo(AgentExecutor):
        async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
            found: dict[str, object] = {}
            message = context.message
            if message is not None:
                for part in message.parts:
                    root = getattr(part, "root", None)
                    if isinstance(root, DataPart):
                        found = dict(root.data)
            updater = TaskUpdater(event_queue, context.task_id or "missing", context.context_id or "missing")
            await updater.add_artifact([Part(root=DataPart(data={"echo": found.get("n")}))], name="echo")
            await updater.complete()

        async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
            updater = TaskUpdater(event_queue, context.task_id or "missing", context.context_id or "missing")
            await updater.update_status(TaskState.canceled, final=True)

    card = AgentCard(
        name="spike-worker",
        description="Echo a DataPart.",
        url=f"http://127.0.0.1:{PORT}/",
        version="0.0.1",
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["application/json"],
        skills=[AgentSkill(id="implement_task", name="implement_task", description="Echo", tags=["spike"])],
    )
    app = A2AStarletteApplication(agent_card=card, http_handler=DefaultRequestHandler(Echo(), InMemoryTaskStore()))
    uvicorn.run(app.build(), host="127.0.0.1", port=PORT, log_level="error")


async def once(n: int, session_service: object) -> bool:
    from google.adk.a2a.agent import RemoteA2aAgent
    from google.adk.runners import Runner
    from google.genai import types
    from a2a.types import DataPart

    agent = RemoteA2aAgent(name=f"worker_{n}", agent_card=CARD, description="echo")
    runner = Runner(app_name="s4", agent=agent, session_service=session_service)  # type: ignore[arg-type]
    await session_service.create_session(app_name="s4", user_id="u", session_id=f"s{n}")  # type: ignore[attr-defined]
    raw = DataPart(data={"n": n}).model_dump_json().encode()
    message = types.Content(
        role="user",
        parts=[types.Part(inline_data=types.Blob(mime_type="text/plain", data=TAG_S + raw + TAG_E))],
    )
    blob = ""
    async for event in runner.run_async(user_id="u", session_id=f"s{n}", new_message=message):
        blob += event.model_dump_json()
    ok = f'"echo": {n}' in blob or f'"echo":{n}' in blob
    if not ok:
        print(f"n={n} events_tail={blob[-300:]!r}")
    return ok


async def clients() -> None:
    import httpx
    from google.adk.sessions import InMemorySessionService

    deadline = time.monotonic() + 20
    async with httpx.AsyncClient() as http:
        while time.monotonic() < deadline:
            try:
                response = await http.get(CARD)
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                await asyncio.sleep(0.2)
        else:
            print("Result: FAIL")
            return
    service = InMemorySessionService()
    first, second = await asyncio.gather(once(1, service), once(2, service))
    print(f"parallel_echo={first and second}")
    if first and second:
        print("Result: PASS")
    else:
        print("Result: FALLBACK Call the worker with the A2A SDK client from FleetAgent")


def main() -> None:
    print(f"google-adk {version('google-adk')}")
    print(f"a2a-sdk {version('a2a-sdk')}")
    if "--server" in sys.argv:
        serve()
        return
    log = open("spikes/s4-server.log", "w", encoding="utf-8")  # noqa: SIM115
    proc = subprocess.Popen([sys.executable, __file__, "--server"], shell=False, stderr=log, stdout=log)
    try:
        asyncio.run(clients())
    except Exception as exc:
        print(f"error={type(exc).__name__}: {exc}")
        print("Result: FAIL")
    finally:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], shell=False, capture_output=True)
        log.close()
        os._exit(0)


if __name__ == "__main__":
    main()
