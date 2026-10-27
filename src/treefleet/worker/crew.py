"""One CrewAI crew. The model returns text. Plain code commits."""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from crewai import Agent, Crew, Process, Task
from crewai.llms.base_llm import BaseLLM
from crewai.mcp.client import MCPClient
from crewai.mcp.transports.stdio import StdioTransport
from crewai.tools import BaseTool
from pydantic import ValidationError

from treefleet.checks import run_command
from treefleet.contracts import (
    CheckResult,
    CursorCall,
    CursorUsage,
    FileChange,
    RunEvent,
    SelfReportedIsolation,
    TokenUsage,
    WorkerDraft,
    WorkerResult,
    WorkerTaskEnvelope,
)
from treefleet.cursor_run import demo_break
from treefleet.events import EventSink
from treefleet.git.cursor_scope import ScopeViolation, check_cursor_scope
from treefleet.git.proofs import revert_out_of_scope, snapshot_main, verify_main
from treefleet.git.worktrees import git_out, run_git
from treefleet.guards.artifacts import revert_undeclared
from treefleet.guards.cost import usage_from_object
from treefleet.guards.draft import draft_problems
from treefleet.mcp_servers.worktree_fs_server import handshake, mcp_env, mcp_stdio
from treefleet.timing import record_step, reset_steps
from treefleet.worker.callbacks import emit_step, emit_task, on_tool_args, on_tool_result
from treefleet.worker.cursor_tool import make_cursor_tool
from treefleet.worker.draft import parse_draft
from treefleet.worker.facts import summary_from_files

cursor_usages: list[CursorUsage] = []
cursor_files: list[str] = []
last_reverted: list[str] = []
tools_seen: list[str] = []


def _schema_name(tools: object, needle: str) -> str | None:
    if not isinstance(tools, list):
        return None
    for tool in tools:
        name = ""
        if isinstance(tool, dict):
            fn = tool.get("function")
            if isinstance(fn, dict) and isinstance(fn.get("name"), str):
                name = fn["name"]
            elif isinstance(tool.get("name"), str):
                name = tool["name"]
        else:
            fn = getattr(tool, "function", None)
            raw = getattr(fn, "name", None) if fn is not None else getattr(tool, "name", None)
            name = raw if isinstance(raw, str) else ""
        if needle in name:
            return name
    return None


class MockLLM(BaseLLM):
    """Recorded offline turns. First turn calls cursor_code. Later turns return WorkerDraft JSON."""

    calls: int = 0

    def supports_function_calling(self) -> bool:
        return True

    def call(  # noqa: ANN001
        self,
        messages,
        tools=None,
        callbacks=None,
        available_functions=None,
        from_task=None,
        from_agent=None,
        response_model=None,
    ):
        self.calls += 1
        count_path = os.environ.get("TREEFLEET_MOCK_COUNT")
        if count_path:
            Path(count_path).write_text(str(self.calls), encoding="utf-8")
        mode = os.environ.get("TREEFLEET_MOCK_LLM")
        if mode == "bad":
            return "not json"
        if mode == "edit-bad" and self.calls > 2:
            return "not json"
        if mode == "nodiff":
            return '{"notes": "no diff read"}'
        if mode == "long" and self.calls > 2:
            return json.dumps({"notes": "x" * 3001})
        if self.calls == 1:
            return [{"name": "cursor_code", "arguments": {"instruction": "Update README.md."}}]
        if self.calls == 2:
            name = _schema_name(tools, "git_diff")
            if name:
                return [{"name": name, "arguments": {"path": "README.md"}}]
        return '{"notes": "Updated README.md."}'

    async def acall(  # noqa: ANN001
        self,
        messages,
        tools=None,
        callbacks=None,
        available_functions=None,
        from_task=None,
        from_agent=None,
        response_model=None,
    ):
        return self.call(
            messages,
            tools,
            callbacks,
            available_functions,
            from_task,
            from_agent,
            response_model,
        )


def tool_names(agent: Agent) -> list[str]:
    names = [getattr(item, "name", "") for item in agent.tools or []]
    return [name for name in names if name]


class _FsTool(BaseTool):
    """One filesystem tool under its real MCP name. The command line is not part of the name."""

    def __init__(
        self,
        tool_name: str,
        factory: Callable[[], MCPClient],
        *,
        root: Path,
        checks: dict[str, str],
        sink: EventSink | None,
        run_id: str,
        task_id: str,
    ) -> None:
        super().__init__(name=tool_name, description=tool_name)
        self._factory = factory
        self._tool_name = tool_name
        self._root = root
        self._checks = checks
        self._sink = sink
        self._run_id = run_id
        self._task_id = task_id

    def _run(self, **kwargs: Any) -> str:
        denial = on_tool_args(
            self._tool_name,
            kwargs,
            root=self._root,
            checks=self._checks,
            sink=self._sink,
            run_id=self._run_id,
            task_id=self._task_id,
        )
        if denial is not None:
            return denial
        client = self._factory()

        async def once() -> str:
            await client.connect()
            try:
                result = await client.call_tool(self._tool_name, kwargs)
            finally:
                await client.disconnect()
            return result if isinstance(result, str) else str(result)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            raw = asyncio.run(once())
        else:
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                raw = pool.submit(asyncio.run, once()).result()
        return on_tool_result(raw, self._sink, self._run_id, self._task_id)


def _fs_tools(envelope: WorkerTaskEnvelope, worktree: Path) -> list[_FsTool]:
    command, args = mcp_stdio()
    env = mcp_env(worktree, envelope.checks)
    timeout_s = envelope.limits.mcp_startup_s
    names = handshake(worktree, envelope.checks, timeout_s, python=Path(command))
    if "git_diff" not in names:
        raise RuntimeError("MCP server did not expose git_diff")

    def factory() -> MCPClient:
        return MCPClient(
            StdioTransport(command=command, args=list(args), env=env),
            connect_timeout=timeout_s,
        )

    sink = _sink()
    return [
        _FsTool(
            name,
            factory,
            root=worktree,
            checks=dict(envelope.checks),
            sink=sink,
            run_id=envelope.run_id,
            task_id=envelope.task.id,
        )
        for name in names
    ]


def _timed(inner: Any, run_id: str) -> BaseLLM:
    global _current_llm
    _current_llm = inner

    class TimingLLM(BaseLLM):
        def supports_function_calling(self) -> bool:
            fn = getattr(inner, "supports_function_calling", None)
            return bool(callable(fn) and fn())

        def call(  # noqa: ANN001
            self,
            messages,
            tools=None,
            callbacks=None,
            available_functions=None,
            from_task=None,
            from_agent=None,
            response_model=None,
        ):
            started = time.monotonic()
            try:
                return inner.call(
                    messages,
                    tools,
                    callbacks,
                    available_functions,
                    from_task,
                    from_agent,
                    response_model,
                )
            finally:
                record_step("deepseek", time.monotonic() - started, name="deepseek", sink=_sink(), run_id=run_id)

        async def acall(  # noqa: ANN001
            self,
            messages,
            tools=None,
            callbacks=None,
            available_functions=None,
            from_task=None,
            from_agent=None,
            response_model=None,
        ):
            return self.call(
                messages,
                tools,
                callbacks,
                available_functions,
                from_task,
                from_agent,
                response_model,
            )

    return TimingLLM(model=str(getattr(inner, "model", "deepseek/deepseek-flash")))


def build_agent(
    envelope: WorkerTaskEnvelope,
    worktree: Path,
    usages: list[CursorUsage] | None = None,
) -> Agent:
    cli = Path(os.environ.get("TREEFLEET_CURSOR_CLI", "cursor-agent"))
    cursor = make_cursor_tool(
        cli=cli,
        worktree=worktree,
        allowed=list(envelope.task.allowed_paths),
        denied=list(envelope.task.denied_paths),
        model=envelope.cursor_model,
        timeout_s=envelope.limits.timeout_s,
        sink=_sink(),
        run_id=envelope.run_id,
        usages=usages,
        files=cursor_files,
        revision=envelope.revision_feedback or "",
        saboteur=envelope.saboteur,
    )
    fs_tools = _fs_tools(envelope, worktree)
    raw = MockLLM(model="mock") if os.environ.get("TREEFLEET_MOCK_LLM") else _deepseek()
    llm = _timed(raw, envelope.run_id)
    return Agent(
        role="worker",
        goal="Direct cursor-agent inside this worktree and do not edit files yourself.",
        backstory="You have no write tool. cursor_code is the only way a file changes.",
        llm=llm,
        tools=[cursor, *fs_tools],
        verbose=False,
        max_iter=4,
    )


def _deepseek():
    from crewai import LLM

    return LLM(
        model="deepseek/deepseek-flash",
        api_key=os.environ.get("DEEPSEEK_API_KEY"),
        timeout=180,
    )


def _main_repo(worktree: Path) -> Path:
    text = git_out(["rev-parse", "--git-common-dir"], worktree).strip()
    common = Path(text)
    if not common.is_absolute():
        common = (worktree / common).resolve()
    return common.parent


def _checks(envelope: WorkerTaskEnvelope, worktree: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    for name, command in envelope.checks.items():
        started = time.monotonic()
        code, out, err = run_command(command, worktree)
        results.append(
            CheckResult(
                name=name,
                command=command,
                exit_code=code,
                passed=code == 0,
                stdout_tail=out[-4000:],
                stderr_tail=err[-4000:],
                duration_s=round(time.monotonic() - started, 3),
            )
        )
        record_step("check", results[-1].duration_s, name=name, sink=_sink(), run_id=envelope.run_id)
    return results


def _commit(worktree: Path, envelope: WorkerTaskEnvelope) -> str | None:
    global last_reverted
    last_reverted = revert_undeclared(worktree, list(envelope.task.allowed_paths), cursor_files)
    if last_reverted:
        sink = _sink()
        if sink is not None:
            sink.emit_sync(
                RunEvent(
                    run_id=envelope.run_id,
                    seq=0,
                    source="git",
                    type="generated_artifact_reverted",
                    task_id=envelope.task.id,
                    payload={"paths": last_reverted[:20]},
                )
            )
    dirty = git_out(["status", "--porcelain"], worktree).strip()
    if not dirty:
        return None
    run_git(["add", "-A"], cwd=worktree)
    proc = run_git(["commit", "-m", f"tf {envelope.task.id}"], cwd=worktree)
    if proc.returncode != 0:
        return None
    return git_out(["rev-parse", "HEAD"], worktree).strip()


def _task_base(worktree: Path, base: str | None) -> str:
    if base:
        return base
    parent = run_git(["rev-parse", "HEAD^"], cwd=worktree)
    if parent.returncode == 0 and parent.stdout.strip():
        return parent.stdout.strip()
    return "HEAD"


def _files(worktree: Path, base: str) -> list[FileChange]:
    """Files changed from the run's base commit through HEAD, including every revision commit."""
    proc = run_git(["diff", "--numstat", f"{base}..HEAD"], cwd=worktree)
    if proc.returncode != 0:
        return []
    found: list[FileChange] = []
    for line in proc.stdout.splitlines():
        added, deleted, path = line.split("\t", 2)
        found.append(
            FileChange(
                path=path,
                additions=0 if added == "-" else int(added),
                deletions=0 if deleted == "-" else int(deleted),
            )
        )
    return found


_brain_parts: list[TokenUsage] = []
_brain_installed = False
_current_llm: Any = None
_from_crew = False


def reset_brain() -> None:
    global _from_crew
    _brain_parts.clear()
    _from_crew = False


def remember_crew(metrics: object) -> None:
    """Prefer CrewAI token_usage over a callback that recorded the same calls."""
    global _from_crew
    found = usage_from_object(metrics)
    if found is None:
        return
    if not _from_crew:
        _brain_parts.clear()
        _from_crew = True
    _brain_parts.append(found)


def _remember(response: object) -> None:
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return
    if not isinstance(usage, dict):
        usage = {
            "prompt_tokens": getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", 0),
            "cached": getattr(usage, "prompt_cache_hit_tokens", None) or getattr(usage, "cached_tokens", 0),
        }
    if _from_crew:
        return
    prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    cached = int(usage.get("prompt_cache_hit_tokens") or usage.get("cached_tokens") or usage.get("cached") or 0)
    if prompt == 0 and completion == 0 and cached == 0:
        return
    _brain_parts.append(TokenUsage(input_tokens=prompt, output_tokens=completion, cached_input_tokens=cached))


def install_brain_capture() -> None:
    """Record DeepSeek token counts. Cursor usage stays on CursorUsage and is never copied here."""
    global _brain_installed
    if _brain_installed:
        return
    try:
        import litellm

        def _on_success(_kwargs: object, response_obj: object, _start: object, _end: object) -> None:
            _remember(response_obj)

        callbacks = list(getattr(litellm, "success_callback", None) or [])
        callbacks.append(_on_success)
        litellm.success_callback = callbacks
    except Exception:
        return
    _brain_installed = True


def take_brain() -> tuple[TokenUsage, bool]:
    if not _brain_parts and _current_llm is not None:
        _remember(getattr(_current_llm, "usage", None))
        inner = getattr(_current_llm, "_inner", None)
        if inner is not None:
            _remember(getattr(inner, "usage", None))
    if not _brain_parts:
        return TokenUsage(), False
    total = TokenUsage()
    for item in _brain_parts:
        total = total + item
    _brain_parts.clear()
    return total, True


def run_task(envelope: WorkerTaskEnvelope, worktree: Path) -> WorkerResult:
    started = time.monotonic()
    reset_brain()
    if not os.environ.get("TREEFLEET_MOCK_LLM"):
        install_brain_capture()
    reset_steps()
    tools_seen.clear()
    _watch_tools()
    main = _main_repo(worktree)
    before = snapshot_main(main)
    cursor_usages.clear()
    cursor_files.clear()
    last_reverted.clear()
    try:
        check_cursor_scope(worktree, run_id=envelope.run_id, sink=_sink())
        agent = build_agent(envelope, worktree, cursor_usages)
    except ScopeViolation as exc:
        return _result(envelope, worktree, "failed", str(exc)[:2000], None, [], started)
    except TimeoutError as exc:
        return _result(envelope, worktree, "failed", str(exc)[:2000], None, [], started)
    record_step("start", time.monotonic() - started, name="start", sink=_sink(), run_id=envelope.run_id)
    feedback = envelope.revision_feedback or ""

    def produce(error: str | None) -> str:
        description = (
            f"{envelope.task.instructions}\nAllowed: {envelope.task.allowed_paths}. "
            f"Denied: {envelope.task.denied_paths}. Revision: {feedback}. "
            "Call cursor_code at most once. Then call git_diff. "
            'Then return JSON {"notes": "..."} only. Do not explore the repository.'
        )
        if error:
            description += f"\nThe previous JSON failed validation: {error}. Return WorkerDraft JSON only."
        task_started = time.monotonic()
        task = Task(
            description=description,
            expected_output="WorkerDraft JSON with notes, after git_diff.",
            agent=agent,
            guardrail=_guard(main, before, worktree, envelope),
            guardrail_max_retries=1,
            callback=lambda _output: emit_task(_sink(), envelope.run_id, envelope.task.id, task_started),
        )
        try:
            output = Crew(
                agents=[agent],
                tasks=[task],
                process=Process.sequential,
                verbose=False,
                step_callback=lambda step: emit_step(
                    _sink(), envelope.run_id, envelope.task.id, type(step).__name__, task_started
                ),
            ).kickoff()
            remember_crew(getattr(output, "token_usage", None))
            return str(output)
        except Exception as exc:
            return str(exc)

    text = produce(None)
    revert_out_of_scope(worktree, list(envelope.task.allowed_paths), list(envelope.task.denied_paths))
    if _main_changed(main, before):
        return _with_usage(
            _result(
                envelope,
                worktree,
                "failed",
                "a write outside the worktree changed the main checkout",
                None,
                [],
                started,
            ),
            cursor_usages,
        )
    try:
        draft = _take(text)
    except (ValidationError, _NeedsDiff) as first:
        try:
            draft = _take(produce(str(first)))
        except (ValidationError, _NeedsDiff) as exc:
            if not _changed(worktree) and not envelope.saboteur:
                return _with_usage(
                    _result(envelope, worktree, "failed", str(exc)[:2000], None, [], started),
                    cursor_usages,
                )
            draft = WorkerDraft(notes="The model notes were not valid JSON. The committed diff is the result.")
    if envelope.saboteur:
        demo_break(worktree)
    checks = _checks(envelope, worktree)
    reverted_scope = _undo_demo_break(worktree) if envelope.saboteur else []
    if any(not item.passed for item in checks):
        return _with_usage(
            _result(
                envelope,
                worktree,
                "failed",
                "a named check failed",
                None,
                checks,
                started,
                notes=draft.notes,
                reverted_scope=reverted_scope,
            ),
            cursor_usages,
        )
    commit_started = time.monotonic()
    sha = _commit(worktree, envelope)
    record_step("commit", time.monotonic() - commit_started, name="commit", sink=_sink(), run_id=envelope.run_id)
    if sha is None:
        return _with_usage(
            _result(
                envelope,
                worktree,
                "failed",
                "no commit was made",
                None,
                checks,
                started,
                notes=draft.notes,
                reverted_scope=reverted_scope,
            ),
            cursor_usages,
        )
    return _with_usage(
        _result(
            envelope, worktree, "done", None, sha, checks, started, notes=draft.notes, reverted_scope=reverted_scope
        ),
        cursor_usages,
    )


def _guard(main: Path, before, worktree: Path, envelope: WorkerTaskEnvelope):
    def check(_output: object) -> tuple[bool, Any]:
        revert_out_of_scope(worktree, list(envelope.task.allowed_paths), list(envelope.task.denied_paths))
        if _main_changed(main, before):
            _note_guard_fail()
            return False, "a write outside the worktree changed the main checkout"
        raw = getattr(_output, "raw", None)
        text = raw if isinstance(raw, str) else str(_output)
        problem = draft_problems(text, _changed(worktree))
        if problem:
            _note_guard_fail()
            return False, problem
        return True, _output

    check.__annotations__["return"] = tuple[bool, Any]
    return check


class _NeedsDiff(Exception):
    pass


def _take(text: str) -> WorkerDraft:
    draft = parse_draft(text)
    if not any("git_diff" in name for name in tools_seen):
        raise _NeedsDiff(f"Call git_diff before notes. Tools: {tools_seen}")
    return draft


def _watch_tools() -> None:
    if getattr(_watch_tools, "on", False):
        return
    from crewai.events.event_bus import crewai_event_bus
    from crewai.events.types.tool_usage_events import ToolUsageFinishedEvent

    def on_tool(_source: object, event: ToolUsageFinishedEvent) -> None:
        tools_seen.append(str(event.tool_name))

    crewai_event_bus.on(ToolUsageFinishedEvent)(on_tool)
    _watch_tools.on = True  # type: ignore[attr-defined]


def _sink() -> EventSink | None:
    root = os.environ.get("TREEFLEET_RUNS")
    return EventSink(Path(root)) if root else None


def _note_guard_fail() -> None:
    path = os.environ.get("TREEFLEET_GUARD_COUNT")
    if not path:
        return
    file = Path(path)
    current = int(file.read_text(encoding="utf-8")) if file.exists() else 0
    file.write_text(str(current + 1), encoding="utf-8")


def _changed(worktree: Path) -> list[str]:
    proc = run_git(["status", "--porcelain"], cwd=worktree)
    found: list[str] = []
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        found.append(line[3:].strip().strip('"').replace("\\", "/"))
    return found


def _main_changed(main: Path, before) -> bool:
    proof = verify_main(main, before)
    return proof.status_before != proof.status_after or proof.head_before != proof.head_after


def _undo_demo_break(worktree: Path) -> list[str]:
    """DEMO ONLY. Restore the existing test after the check has already failed."""
    target = worktree / "tests" / "test_auth.py"
    if not target.is_file():
        return []
    if "test_treefleet_saboteur_demo" not in target.read_text(encoding="utf-8"):
        return []
    run_git(["checkout", "--", "tests/test_auth.py"], cwd=worktree)
    return ["tests/test_auth.py"]


def _with_usage(result: WorkerResult, usages: list[CursorUsage]) -> WorkerResult:
    brain, brain_reported = take_brain()
    calls = [
        CursorCall(
            round=result.attempt,
            model=result.cursor_model or "cursor",
            usage=TokenUsage(
                input_tokens=item.input_tokens,
                output_tokens=item.output_tokens,
                cached_input_tokens=item.cache_read_tokens,
                cache_write_tokens=item.cache_write_tokens,
            ),
            reported=True,
        )
        for item in usages
    ]
    if not calls:
        calls = [CursorCall(round=result.attempt, model=result.cursor_model or "cursor", reported=False)]
    update: dict[str, object] = {
        "brain_usage": brain,
        "brain_usage_reported": brain_reported,
        "cursor_usage_reported": bool(usages),
        "cursor_calls": calls,
    }
    if usages:
        update["usage"] = TokenUsage(
            input_tokens=sum(item.input_tokens for item in usages),
            output_tokens=sum(item.output_tokens for item in usages),
            cached_input_tokens=sum(item.cache_read_tokens for item in usages),
            cache_write_tokens=sum(item.cache_write_tokens for item in usages),
        )
    return result.model_copy(update=update)


def _result(
    envelope: WorkerTaskEnvelope,
    worktree: Path,
    status: str,
    error: str | None,
    sha: str | None,
    checks: list[CheckResult],
    started: float,
    notes: str = "",
    reverted_scope: list[str] | None = None,
) -> WorkerResult:
    branch = git_out(["branch", "--show-current"], worktree).strip()
    files = _files(worktree, _task_base(worktree, envelope.base_sha)) if sha else []
    return WorkerResult(
        run_id=envelope.run_id,
        task_id=envelope.task.id,
        attempt=envelope.attempt,
        status=status,  # type: ignore[arg-type]
        summary=summary_from_files(files),
        notes=notes,
        branch=branch,
        head_sha=sha,
        commit_made=sha is not None,
        files_changed=files,
        checks=checks,
        duration_s=round(time.monotonic() - started, 3),
        model=envelope.model,
        cursor_model=envelope.cursor_model,
        reverted_generated=list(last_reverted),
        reverted_scope=reverted_scope or [],
        isolation=SelfReportedIsolation(
            pid=os.getpid(),
            cwd=str(worktree.resolve()),
            toplevel=git_out(["rev-parse", "--show-toplevel"], worktree).strip(),
            branch=branch,
            mcp_root=str(worktree.resolve()),
        ),
        error=error,
    )
