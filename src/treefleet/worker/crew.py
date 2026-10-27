"""One CrewAI crew. The model returns text. Plain code commits."""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from crewai import Agent, Crew, Process, Task
from crewai.llms.base_llm import BaseLLM
from crewai.mcp import MCPServerStdio
from pydantic import ValidationError

from treefleet.contracts import (
    CheckResult,
    CursorUsage,
    FileChange,
    SelfReportedIsolation,
    TokenUsage,
    WorkerDraft,
    WorkerResult,
    WorkerTaskEnvelope,
)
from treefleet.events import EventSink
from treefleet.git.proofs import revert_out_of_scope, snapshot_main, verify_main
from treefleet.git.worktrees import git_out, run_git
from treefleet.mcp_servers.worktree_fs_server import mcp_env, mcp_stdio
from treefleet.timing import record_step, reset_steps
from treefleet.worker.cursor_tool import make_cursor_tool
from treefleet.worker.draft import parse_draft
from treefleet.worker.facts import summary_from_files

cursor_usages: list[CursorUsage] = []
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
        if mode == "nodiff":
            return '{"notes": "no diff read"}'
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


def _timed(inner: Any, run_id: str) -> BaseLLM:
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
    )
    command, mcp_args = mcp_stdio()
    mcp = MCPServerStdio(
        command=command,
        args=mcp_args,
        env=mcp_env(worktree, envelope.checks),
        cache_tools_list=True,
    )
    raw = MockLLM(model="mock") if os.environ.get("TREEFLEET_MOCK_LLM") else _deepseek()
    llm = _timed(raw, envelope.run_id)
    return Agent(
        role="worker",
        goal="Direct cursor-agent inside this worktree and do not edit files yourself.",
        backstory="You have no write tool. cursor_code is the only way a file changes.",
        llm=llm,
        tools=[cursor],
        mcps=[mcp],
        verbose=False,
        max_iter=4,
    )


def _deepseek():
    from crewai import LLM

    return LLM(model="deepseek/deepseek-flash", api_key=os.environ.get("DEEPSEEK_API_KEY"))


def _main_repo(worktree: Path) -> Path:
    text = git_out(["rev-parse", "--git-common-dir"], worktree).strip()
    common = Path(text)
    if not common.is_absolute():
        common = (worktree / common).resolve()
    return common.parent


def _checks(envelope: WorkerTaskEnvelope, worktree: Path) -> list[CheckResult]:
    results: list[CheckResult] = []
    for name, command in envelope.checks.items():
        argv = shlex.split(command, posix=os.name != "nt")
        started = time.monotonic()
        completed = subprocess.run(
            argv,
            cwd=worktree,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
            check=False,
        )
        results.append(
            CheckResult(
                name=name,
                command=command,
                exit_code=completed.returncode,
                passed=completed.returncode == 0,
                stdout_tail=completed.stdout[-4000:],
                stderr_tail=completed.stderr[-4000:],
                duration_s=round(time.monotonic() - started, 3),
            )
        )
        record_step("check", results[-1].duration_s, name=name, sink=_sink(), run_id=envelope.run_id)
    return results


def _commit(worktree: Path, envelope: WorkerTaskEnvelope) -> str | None:
    dirty = git_out(["status", "--porcelain"], worktree).strip()
    if not dirty:
        return None
    run_git(["add", "-A"], cwd=worktree)
    proc = run_git(["commit", "-m", f"tf {envelope.task.id}"], cwd=worktree)
    if proc.returncode != 0:
        return None
    return git_out(["rev-parse", "HEAD"], worktree).strip()


def _files(worktree: Path, base: str) -> list[FileChange]:
    proc = run_git(["diff", "--numstat", f"{base}...HEAD"], cwd=worktree)
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


def run_task(envelope: WorkerTaskEnvelope, worktree: Path) -> WorkerResult:
    started = time.monotonic()
    reset_steps()
    tools_seen.clear()
    _watch_tools()
    main = _main_repo(worktree)
    before = snapshot_main(main)
    cursor_usages.clear()
    agent = build_agent(envelope, worktree, cursor_usages)
    record_step("start", time.monotonic() - started, name="start", sink=_sink(), run_id=envelope.run_id)
    feedback = envelope.revision_feedback or ""

    def produce(error: str | None) -> str:
        description = (
            f"{envelope.task.instructions}\nAllowed: {envelope.task.allowed_paths}. "
            f"Denied: {envelope.task.denied_paths}. Revision: {feedback}. "
            "Call cursor_code at most once. Then call the tool whose name ends with git_diff. "
            'Then return JSON {"notes": "..."} only. Do not explore the repository.'
        )
        if error:
            description += f"\nThe previous JSON failed validation: {error}. Return WorkerDraft JSON only."
        task = Task(
            description=description,
            expected_output="WorkerDraft JSON with notes, after git_diff.",
            agent=agent,
            guardrail=_guard(main, before, worktree, envelope),
            guardrail_max_retries=1,
        )
        try:
            return str(Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False).kickoff())
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
            return _with_usage(
                _result(envelope, worktree, "failed", str(exc)[:2000], None, [], started),
                cursor_usages,
            )
    checks = _checks(envelope, worktree)
    if any(not item.passed for item in checks):
        return _with_usage(
            _result(envelope, worktree, "failed", "a named check failed", None, checks, started, notes=draft.notes),
            cursor_usages,
        )
    commit_started = time.monotonic()
    sha = _commit(worktree, envelope)
    record_step("commit", time.monotonic() - commit_started, name="commit", sink=_sink(), run_id=envelope.run_id)
    if sha is None:
        return _with_usage(
            _result(envelope, worktree, "failed", "no commit was made", None, checks, started, notes=draft.notes),
            cursor_usages,
        )
    return _with_usage(
        _result(envelope, worktree, "done", None, sha, checks, started, notes=draft.notes),
        cursor_usages,
    )


def _guard(main: Path, before, worktree: Path, envelope: WorkerTaskEnvelope):
    def check(_output: object) -> tuple[bool, Any]:
        revert_out_of_scope(worktree, list(envelope.task.allowed_paths), list(envelope.task.denied_paths))
        if _main_changed(main, before):
            _note_guard_fail()
            return False, "a write outside the worktree changed the main checkout"
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


def _main_changed(main: Path, before) -> bool:
    proof = verify_main(main, before)
    return proof.status_before != proof.status_after or proof.head_before != proof.head_after


def _with_usage(result: WorkerResult, usages: list[CursorUsage]) -> WorkerResult:
    if not usages:
        return result
    return result.model_copy(
        update={
            "usage": TokenUsage(
                input_tokens=sum(item.input_tokens for item in usages),
                output_tokens=sum(item.output_tokens for item in usages),
                cached_input_tokens=sum(item.cache_read_tokens for item in usages),
            )
        }
    )


def _result(
    envelope: WorkerTaskEnvelope,
    worktree: Path,
    status: str,
    error: str | None,
    sha: str | None,
    checks: list[CheckResult],
    started: float,
    notes: str = "",
) -> WorkerResult:
    branch = git_out(["branch", "--show-current"], worktree).strip()
    files = _files(worktree, "main") if sha else []
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
        isolation=SelfReportedIsolation(
            pid=os.getpid(),
            cwd=str(worktree.resolve()),
            toplevel=git_out(["rev-parse", "--show-toplevel"], worktree).strip(),
            branch=branch,
            mcp_root=str(worktree.resolve()),
        ),
        error=error,
    )
