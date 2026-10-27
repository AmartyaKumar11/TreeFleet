"""A2A SDK app. The executor runs the crew. One task at a time."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps.jsonrpc.starlette_app import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentSkill, DataPart, Part, TaskState

from treefleet.contracts import WorkerResult, WorkerTaskEnvelope
from treefleet.worker.crew import run_task


class WorkerExecutor(AgentExecutor):
    def __init__(self, worktree: Path) -> None:
        self._worktree = worktree
        self._lock = threading.Lock()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id if isinstance(context.task_id, str) else "unset"
        context_id = context.context_id if isinstance(context.context_id, str) else task_id
        updater = TaskUpdater(event_queue, task_id, context_id)
        if not self._lock.acquire(blocking=False):
            await updater.update_status(TaskState.rejected, final=True)
            return
        try:
            data = _envelope_data(context)
            envelope = WorkerTaskEnvelope.model_validate(data)
            result = await asyncio.to_thread(run_task, envelope, self._worktree)
            await updater.add_artifact(
                [Part(root=DataPart(data=result.model_dump(mode="json")))],
                name="result",
            )
            if result.status == "done":
                await updater.complete()
            else:
                await updater.failed()
        except Exception:
            await updater.failed()
        finally:
            self._lock.release()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task_id = context.task_id if isinstance(context.task_id, str) else "unset"
        context_id = context.context_id if isinstance(context.context_id, str) else task_id
        updater = TaskUpdater(event_queue, task_id, context_id)
        await updater.cancel()


def _envelope_data(context: RequestContext) -> dict:
    message = context.message
    if message is None:
        raise ValueError("missing message")
    for part in message.parts:
        root = part.root
        if isinstance(root, DataPart):
            return dict(root.data)
    raise ValueError("missing WorkerTaskEnvelope")


def build_card(task_id: str, url: str) -> AgentCard:
    return AgentCard(
        name=f"treefleet-worker-{task_id}",
        description="Implements one scoped task inside a single git worktree by directing a headless Cursor agent.",
        url=url,
        version="1",
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=[
            AgentSkill(
                id="implement_task",
                name="Implement task",
                description="Accepts a WorkerTaskEnvelope, directs cursor-agent, commits, and returns a WorkerResult.",
                tags=["code"],
            )
        ],
    )


def build_app(worktree: Path, task_id: str, url: str) -> A2AStarletteApplication:
    handler = DefaultRequestHandler(agent_executor=WorkerExecutor(worktree), task_store=InMemoryTaskStore())
    return A2AStarletteApplication(agent_card=build_card(task_id, url), http_handler=handler)


def bearer_app(inner, token: str):
    async def app(scope, receive, send):
        if scope.get("type") == "http":
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            if headers.get(b"authorization") != f"Bearer {token}".encode():
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b"unauthorized"})
                return
        await inner(scope, receive, send)

    return app


def result_from_artifact(payload: dict) -> WorkerResult:
    return WorkerResult.model_validate(payload)
