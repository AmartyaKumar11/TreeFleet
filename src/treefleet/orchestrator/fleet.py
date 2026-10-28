"""FleetAgent: worktrees, worker processes, and one A2A call per task."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from treefleet.checks import python_prefix
from treefleet.contracts import (
    Limits,
    RunEvent,
    SelfReportedIsolation,
    StateKey,
    UpstreamContext,
    WorkerResult,
    WorkerTaskEnvelope,
)
from treefleet.git.canary import write_canary
from treefleet.git.cursor_scope import ScopeViolation
from treefleet.git.proofs import snapshot_main, snapshot_siblings
from treefleet.git.worktrees import GitError, branch_name, create_worktree, kill_tree, run_git
from treefleet.orchestrator.agents import RunBundle, _adk_event, _phase, read_plan, read_routing
from treefleet.worker.spawner import before_spawn, free_port, spawn_worker, worker_env

_WAIT_S = 600
_START = b"<a2a_datapart_json>"


def task_is_saboteur(flag: str | None, task_id: str) -> bool:
    """The named task, including a planner prefix such as add-."""
    return bool(flag) and (task_id == flag or task_id.endswith(flag))


_END = b"</a2a_datapart_json>"


def _event(bundle: RunBundle, kind: str, task_id: str, payload: dict[str, Any]) -> None:
    bundle.sink.emit_sync(
        RunEvent(
            run_id=bundle.run_id,
            seq=0,
            source="orchestrator",
            type=kind,
            task_id=task_id,
            payload=payload,
        )
    )


def _base_sha(repo: Path, base_ref: str) -> str | None:
    proc = run_git(["rev-parse", "--verify", base_ref], cwd=repo)
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha if len(sha) == 40 else None


def _unchanged_sha(worktree: Path) -> str | None:
    """HEAD of a worktree whose task committed nothing. Dependents may build on that commit."""
    if not worktree.is_dir():
        return None
    proc = run_git(["rev-parse", "HEAD"], cwd=worktree)
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha if len(sha) == 40 else None


def _failed(envelope: WorkerTaskEnvelope, worktree: Path | str, message: str, pid: int) -> WorkerResult:
    branch = branch_name(envelope.run_id, envelope.task.id)
    return WorkerResult(
        run_id=envelope.run_id,
        task_id=envelope.task.id,
        attempt=envelope.attempt,
        status="failed",
        branch=branch,
        model=envelope.model,
        cursor_model=envelope.cursor_model,
        isolation=SelfReportedIsolation(
            pid=max(pid, 1),
            cwd=str(worktree),
            toplevel=str(worktree),
            branch=branch,
            mcp_root=str(worktree),
        ),
        error=message[:2000],
    )


def _result_from_blob(raw: bytes) -> WorkerResult | None:
    if not (raw.startswith(_START) and raw.endswith(_END)):
        return None
    payload = json.loads(raw[len(_START) : -len(_END)])
    inner = payload.get("data", payload) if isinstance(payload, dict) else None
    if isinstance(inner, dict) and inner.get("schema_id") == "treefleet.worker_result.v1":
        return WorkerResult.model_validate(inner)
    return None


def _from_remote_event(event: Any) -> WorkerResult | None:
    content = getattr(event, "content", None)
    if content is None:
        return None
    for part in content.parts or []:
        blob = getattr(part, "inline_data", None)
        data = getattr(blob, "data", None) if blob is not None else None
        if isinstance(data, str):
            data = data.encode()
        if isinstance(data, bytes):
            found = _result_from_blob(data)
            if found is not None:
                return found
    return None


async def _via_remote(url: str, token: str, envelope: WorkerTaskEnvelope) -> WorkerResult:
    import httpx
    from a2a.types import DataPart
    from google.adk.a2a.agent import RemoteA2aAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    raw = DataPart(data=envelope.model_dump(mode="json")).model_dump_json().encode()
    message = types.Content(
        role="user",
        parts=[types.Part(inline_data=types.Blob(mime_type="text/plain", data=_START + raw + _END))],
    )
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers, timeout=_WAIT_S) as http:
        remote_cls: Any = RemoteA2aAgent
        agent = remote_cls(
            name="w_" + envelope.task.id.replace("-", "_"),
            agent_card=url + "/.well-known/agent-card.json",
            description="One TreeFleet worker.",
            httpx_client=http,
            timeout=_WAIT_S,
        )
        service = InMemorySessionService()
        session_id = "s_" + envelope.task.id.replace("-", "_")
        await service.create_session(app_name="fleet", user_id="u", session_id=session_id)
        runner = Runner(app_name="fleet", agent=agent, session_service=service)
        found: WorkerResult | None = None
        async for event in runner.run_async(user_id="u", session_id=session_id, new_message=message):
            parsed = _from_remote_event(event)
            if parsed is not None:
                found = parsed
    if found is None:
        raise RuntimeError("RemoteA2aAgent returned no WorkerResult")
    return found


async def _via_sdk(url: str, token: str, envelope: WorkerTaskEnvelope) -> WorkerResult:
    import httpx
    from a2a.client import A2AClient
    from a2a.types import DataPart, Message, MessageSendParams, Part, Role, SendMessageRequest, Task

    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(headers=headers, timeout=_WAIT_S) as http:
        client = A2AClient(http, url=url)
        message = Message(
            message_id=str(uuid.uuid4()),
            role=Role.user,
            parts=[Part(root=DataPart(data=envelope.model_dump(mode="json")))],
        )
        response = await client.send_message(SendMessageRequest(id="1", params=MessageSendParams(message=message)))
    root = getattr(response, "root", None)
    result = getattr(root, "result", None)
    if isinstance(result, Task):
        for artifact in result.artifacts or []:
            for part in artifact.parts:
                data = part.root
                if isinstance(data, DataPart):
                    return WorkerResult.model_validate(data.data)
    raise RuntimeError("A2A client returned no WorkerResult")


async def dispatch(url: str, token: str, envelope: WorkerTaskEnvelope) -> tuple[WorkerResult, str]:
    """RemoteA2aAgent first. The A2A SDK client covers a failure S4 did not."""
    try:
        return await _via_remote(url, token, envelope), "remote"
    except Exception as exc:
        note = f"RemoteA2aAgent fallback: {type(exc).__name__}"
        try:
            return await _via_sdk(url, token, envelope), note
        except Exception as second:
            failed = _failed(envelope, url, f"{note}; {type(second).__name__}: {second}", 1)
            return failed, note


def _extra_env(bundle: RunBundle) -> dict[str, str]:
    env = {"TREEFLEET_RUNS": str(bundle.runs_root), "TREEFLEET_CURSOR_CLI": bundle.settings.cursor.cli}
    fakes = bundle.fakes
    if fakes is None:
        return env
    if fakes.cursor_cli is not None:
        env["TREEFLEET_CURSOR_CLI"] = str(fakes.cursor_cli)
    if fakes.mock_llm:
        env["TREEFLEET_MOCK_LLM"] = fakes.mock_llm
    if fakes.fake_edit:
        env["TREEFLEET_FAKE_EDIT"] = fakes.fake_edit
    if fakes.fake_sleep:
        env["TREEFLEET_FAKE_SLEEP"] = fakes.fake_sleep
    return env


def _fleet_base():
    from google.adk.agents import BaseAgent

    class FleetAgent(BaseAgent):
        bundle: Any

        async def _run_async_impl(self, ctx: Any) -> AsyncGenerator[Any, None]:
            if not self.bundle.approved:
                return
            write_canary(self.bundle.repo, self.bundle.run_id)
            self.bundle.main_before = snapshot_main(self.bundle.repo)
            self.bundle.siblings_before = snapshot_siblings(self.bundle.repo)
            self.bundle.fleet_started = time.monotonic()
            plan = read_plan(ctx.session.state)
            routes = {item.task_id: item for item in read_routing(ctx.session.state)}
            results: dict[str, WorkerResult] = {}
            try:
                for wave in plan.waves():
                    tasks = [task for task in plan.tasks if task.id in wave]
                    limit = self.bundle.request.max_workers
                    for offset in range(0, len(tasks), limit):
                        chunk = tasks[offset : offset + limit]
                        done = await asyncio.gather(*[self._one(task, results, routes) for task in chunk])
                        for item in done:
                            results[item.task_id] = item
            finally:
                self.bundle.fleet_ended = time.monotonic()
                _phase(self.bundle, "fleet", self.bundle.fleet_ended - self.bundle.fleet_started)
            yield _adk_event(
                ctx,
                self.name,
                {
                    StateKey.WORKER_RESULTS: {key: value.model_dump(mode="json") for key, value in results.items()},
                    StateKey.ATTEMPTS: {key: value.attempt for key, value in results.items()},
                },
            )

        async def _one(self, task: Any, results: dict[str, WorkerResult], routes: dict[str, Any]) -> WorkerResult:
            route = routes[task.id]
            upstream: list[UpstreamContext] = []
            for dep in task.depends_on:
                prev = results.get(dep)
                sha = prev.head_sha if prev is not None else None
                # A finished task with no commit still has the base HEAD. A main-checkout
                # mutation stays fail-closed and does not unblock anyone.
                if prev is not None and not sha and "main checkout" not in (prev.error or ""):
                    sha = _unchanged_sha(self.bundle.worktree_root / self.bundle.run_id / dep)
                if prev is None or not sha:
                    envelope = self._envelope(task, route, [])
                    return _failed(envelope, self.bundle.repo, f"upstream {dep} did not finish", 1)
                upstream.append(
                    UpstreamContext(
                        task_id=dep,
                        summary=prev.summary,
                        branch=prev.branch,
                        head_sha=sha,
                    )
                )
            envelope = self._envelope(task, route, upstream)
            try:
                worktree = create_worktree(
                    self.bundle.repo,
                    self.bundle.run_id,
                    task.id,
                    self.bundle.worktree_root,
                    self.bundle.request.base_ref,
                )
            except GitError as exc:
                return _failed(envelope, self.bundle.repo, str(exc), 1)
            try:
                before_spawn(worktree, self.bundle.run_id, self.bundle.sink, task.id)
            except ScopeViolation as exc:
                return _failed(envelope, worktree, str(exc), 1)
            port = free_port(self.bundle.port_start, self.bundle.ports)
            self.bundle.ports.add(port)
            extra = _extra_env(self.bundle)
            prefix = python_prefix(dict(self.bundle.request.checks))
            if prefix:
                extra = {**extra, "TREEFLEET_PYTHON_DIR": prefix}
            try:
                proc = await asyncio.to_thread(
                    spawn_worker,
                    python=Path(self.bundle.settings.worker_python),
                    worktree=worktree,
                    run_id=self.bundle.run_id,
                    task_id=task.id,
                    token=self.bundle.token,
                    port=port,
                    env=worker_env(self.bundle.token, extra),
                    log_path=self.bundle.runs_root / self.bundle.run_id / f"{task.id}.log",
                    owner=self.bundle.procs,
                )
            except (OSError, RuntimeError, TimeoutError) as exc:
                return _failed(envelope, worktree, str(exc), 1)
            _event(
                self.bundle,
                "worker.spawned",
                task.id,
                {"upstream": [item.summary for item in upstream], "pid": proc.pid},
            )
            fakes = self.bundle.fakes
            if fakes is not None and fakes.kill_task == task.id:
                asyncio.get_running_loop().call_later(0.4, lambda pid=proc.popen.pid: kill_tree(pid))
            self.bundle.workers[task.id] = proc
            self.bundle.envelopes[task.id] = envelope
            result, note = await dispatch(proc.url, self.bundle.token, envelope)
            _event(
                self.bundle,
                "worker.exited",
                task.id,
                {
                    "status": result.status,
                    "files": [item.path for item in result.files_changed],
                    "a2a": note,
                },
            )
            return result

        def _envelope(self, task: Any, route: Any, upstream: list[UpstreamContext]) -> WorkerTaskEnvelope:
            checks = {
                name: self.bundle.request.checks[name] for name in task.check_ids if name in self.bundle.request.checks
            }
            # DEMO ONLY: the break must land in an existing test, which the plan does not allow.
            if task_is_saboteur(self.bundle.request.saboteur_task, task.id) and "tests" not in task.allowed_paths:
                task = task.model_copy(update={"allowed_paths": [*task.allowed_paths, "tests"]})
            return WorkerTaskEnvelope(
                run_id=self.bundle.run_id,
                task=task,
                model=self.bundle.settings.models.fast,
                cursor_model=route.cursor_model,
                checks=checks,
                limits=Limits(timeout_s=_WAIT_S),
                base_sha=_base_sha(self.bundle.repo, self.bundle.request.base_ref),
                upstream=upstream,
                saboteur=task_is_saboteur(self.bundle.request.saboteur_task, task.id),
            )

    return FleetAgent


FleetAgent = _fleet_base()
