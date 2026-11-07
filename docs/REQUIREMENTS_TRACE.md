# Requirements trace

Every row ends with an evidence path or the command that produces it. Phase 11 will enforce existence with a test.

| # | Requirement | How TreeFleet satisfies it | Where | Status | Evidence path or command |
|---|---|---|---|---|---|
| 1 | Context sharing and state updates | ADK `session.state` carries request, plan, routing, worker results, isolation, verdicts, report. Each stage reads only what the previous stage wrote. Workers receive a typed envelope and return a typed result | `contracts.StateKey`, `orchestrator/`, CONTRACTS section 4 | met | `evidence/phase-6/live-clean/events.jsonl` |
| 2 | Tool integration using MCP, at least two external tools | Two custom MCP servers (`worktree-fs-mcp` for workers, `gitops-mcp` for the reviewer) plus two external tools: Cursor CLI as the coding tool and Jev as an HTTP tool, plus git as a library tool | `mcp_servers/`, `jev/` | met | `evidence/phase-6/live-clean/events.jsonl` |
| 3 | Structured output with Pydantic | Plan, WorkerDraft, WorkerResult, ReviewVerdict, FinalReport are Pydantic models. The final output is JSON and a deterministic Markdown report with tables | `contracts.py`, `FinalReport.to_markdown` | met | `evidence/phase-6/live-clean/report.md` |
| 4 | Suitable agentic design patterns | Orchestrator-workers, parallelization, routing, evaluator-optimizer, guardrail, human-in-the-loop hook | ARCHITECTURE section 5 | met | `evidence/phase-6/live-clean/events.jsonl` |
| 5 | Task monitoring and logging through callbacks | ADK before and after agent, model and tool callbacks. CrewAI task callback, guardrail and step hook. All feed one `EventSink` with JSONL and SSE. Langfuse is deferred with requirement 9 | `orchestrator/callbacks.py`, `worker/callbacks.py`, `events.py` | met | `evidence/phase-6/live-clean/events.jsonl` |
| 6 | Agent to agent communication with A2A, interoperability | ADK orchestrator (A2A client) talks to CrewAI workers (A2A servers) through agent cards and `message/send`. Two frameworks, one protocol | `orchestrator/fleet.py`, `worker/a2a_server.py` | met | `evidence/phase-4/events.jsonl` |
| 7 | Use both CrewAI and ADK, no other agentic framework | ADK for the orchestrator, CrewAI for the workers. LiteLLM, the A2A SDK and the MCP SDK are libraries, not agentic frameworks | `pyproject.toml` extras, ARCHITECTURE section 4 | **pending** the professor's written confirmation that Cursor CLI is an external coding tool and not an agentic framework | `pyproject.toml` (dependency split). Written confirmation is not on file yet |
| 8 | Secure the system with guardrails and validation callbacks | Six guard stages. Input parsing for toxic and injection content. Tool argument checks. Type checking of tool returns. Task guardrail on scope. Output redaction | `guards/`, ARCHITECTURE section 8 | met | `evidence/phase-5/guard-demo/b/report.md`, `eval/report.md` |
| 9 | Observability for tracing, auditability, provenance | Deferred. Phase 7 (Langfuse) was not built, so there is no distributed trace tree. The local audit log remains `events.jsonl` | `events.py` | **deferred** — phase 7 not started; no Langfuse keys or instrumentors wired | Local audit only: `evidence/phase-6/live-clean/events.jsonl` |
| 10 | End-to-end performance evaluation, cost and running time estimates | Eval harness with E1–E7, tokens by role, USD per run, wall time versus a sequential estimate. E7 covers same-hunk merge conflict resolve via the owning worker. The 5/10/20 projection is an extrapolation | `src/treefleet/evals/`, `eval/scenarios.toml` | met | `eval/report.md`, `evidence/phase-9/eval-report.md`. Command: `treefleet eval --repo <path>` |
| 11 | Deploy on a cloud platform | Deferred. Phase 10 (Cloud Run / container host) was not built, so there is no service URL, deploy log, or cloud screenshot | not built | **deferred** — phase 10 not started; no Dockerfile deploy or smoke URL | Command when built: see `docs/PHASES.md` phase 10. Until then: local only (`treefleet ui`, `treefleet "goal"`) |

## Demo script

See `docs/DEMO.md` for the local board rehearsal (preflight, reset, two-task goal, replay fallback). Cloud and Langfuse steps from the original course outline stay out until requirements 9 and 11 are no longer deferred.

## Questions to prepare for

| Question | Answer |
|---|---|
| Is this really isolated? | Five layers: separate worktree, separate process with cwd in the worktree, root-locked tools, scope contract, and orchestrator-computed proof. It is not an OS sandbox, and the container is the outer boundary in the cloud |
| Why not let workers talk directly? | Hub and spoke keeps every message guardable and logged. Workers cannot read each other's state |
| Why two frameworks? | Required. ADK orchestrates, CrewAI executes, and A2A shows they interoperate without sharing a process or a dependency set |
| Why does the LLM not run git? | Isolation and commits must be deterministic and auditable. Cursor edits files, plain code commits |
| Who writes the code? | Cursor CLI, one headless agent per worktree, directed by a CrewAI worker. DeepSeek reasons and orchestrates, Jev classifies. This mirrors how AO drives coding agent CLIs |
| Is CrewAI just a wrapper around Cursor? | No. The CrewAI agent plans the task, writes the Cursor instruction, inspects the diff, runs checks, re-prompts on failure, and is subject to guardrails and retries. Cursor is its coding tool |
| What does Jev add? | Cheap typed decisions: guard first pass, routing, scope pre-check, output pruning. It is not the security boundary. Deterministic checks run first |
| How did you validate guardrails? | Labeled corpus with precision and recall, plus direct and indirect injection scenarios |
| What are the limits? | Cursor edits through its own tools so isolation is detected and reverted rather than prevented, Cursor usage is billed by Cursor, headless auth in a container needs verification, DeepSeek tool-calling reliability, merge conflicts get one Cursor resolve attempt then exclude, workers have network access |
