# Requirements trace

Every row must end with an evidence path that exists before submission. Phase 11 enforces that with a test.

| # | Requirement | How TreeFleet satisfies it | Where | Proof to show | Evidence path |
|---|---|---|---|---|---|
| 1 | Context sharing and state updates | ADK `session.state` carries request, plan, routing, worker results, isolation, verdicts, report. Each stage reads only what the previous stage wrote. Workers receive a typed envelope and return a typed result | `contracts.StateKey`, `orchestrator/`, CONTRACTS section 4 | State snapshot after each stage in the event log | `evidence/phase-4/state-trace.jsonl` |
| 2 | Tool integration using MCP, at least two external tools | Two custom MCP servers (`worktree-fs-mcp` for workers, `gitops-mcp` for the reviewer) plus two external tools: Cursor CLI as the coding tool and Jev as an HTTP tool, plus git as a library tool | `mcp_servers/`, `jev/` | MCP call events with server and tool names, Jev call events | `evidence/phase-5/tool-events.jsonl` |
| 3 | Structured output with Pydantic | Plan, WorkerDraft, WorkerResult, ReviewVerdict, FinalReport are Pydantic models. The final output is JSON and a deterministic Markdown report with tables | `contracts.py`, `FinalReport.to_markdown` | `report.json` and `report.md` from a run | `evidence/phase-6/report.md` |
| 4 | Suitable agentic design patterns | Orchestrator-workers, parallelization, routing, evaluator-optimizer, guardrail, human-in-the-loop hook | ARCHITECTURE section 5 | Event timeline showing parallel overlap, a revise loop and a routing decision | `evidence/phase-6/timeline.png` |
| 5 | Task monitoring and logging through callbacks | ADK before and after agent, model and tool callbacks. CrewAI task callback, guardrail and step hook. All feed one `EventSink` with JSONL, SSE and Langfuse | `orchestrator/callbacks.py`, `worker/callbacks.py`, `events.py` | `events.jsonl` with intermediate outputs, execution flow and errors | `evidence/phase-5/events.jsonl` |
| 6 | Agent to agent communication with A2A, interoperability | ADK orchestrator (A2A client) talks to CrewAI workers (A2A servers) through agent cards and `message/send`. Two frameworks, one protocol | `orchestrator/fleet.py`, `worker/a2a_server.py` | Agent card fetched from a worker, A2A request and response captured | `evidence/phase-4/a2a-exchange.json` |
| 7 | Use both CrewAI and ADK, no other agentic framework | ADK for the orchestrator, CrewAI for the workers. LiteLLM, the A2A SDK and the MCP SDK are libraries, not agentic frameworks. Professor confirmation on file | `pyproject.toml` extras, ARCHITECTURE section 4 | Dependency list per environment, written confirmation | `evidence/phase-0/frameworks.txt`, `evidence/phase-0/professor-confirmation.txt` |
| 8 | Secure the system with guardrails and validation callbacks | Six guard stages. Input parsing for toxic and injection content. Tool argument checks. Type checking of tool returns. Task guardrail on scope. Output redaction | `guards/`, ARCHITECTURE section 8 | Guard corpus results, direct and indirect injection scenarios | `evidence/phase-5/guard-report.md`, `evidence/phase-9/eval-report.md` |
| 9 | Observability for tracing, auditability, provenance | Langfuse with OpenInference instrumentors for both frameworks, trace id propagated through A2A, session id per run, branch and commit sha on every result | `obs/tracing.py` | One trace tree per run in Langfuse | `evidence/phase-7/trace.png` |
| 10 | End-to-end performance evaluation, cost and running time estimates | Eval harness with six scenarios, ablations, tokens and USD per component, wall time versus sequential estimate | `evals/` | `eval-report.md` tables | `evidence/phase-9/eval-report.md` |
| 11 | Deploy on a cloud platform | Containerized and deployed on Cloud Run (or the confirmed platform), secrets from Secret Manager, smoke-tested | `deploy/` | Service URL, deploy log, screenshot of a finished run | `evidence/phase-10/` |

## Demo script (about 10 minutes)

1. **Open the deployed UI.** Show the goal form and the PR Sentinel repository. (30 s)
2. **Run S1 same-file-disjoint.** Narrate the plan appearing. Point at three cards, three branches, three worktree paths. (2 min)
3. **Proof panel.** Show live `git worktree list`, the three distinct toplevels, the canary absent in each worktree and the main checkout unchanged. (2 min)
4. **Run S3 saboteur.** One card fails its tests while the others stay green. Show main checkout still clean. (2 min)
5. **Run S5 and S6.** A malicious goal is blocked at the door. A malicious repo file is ignored, and the tool-denial event is shown. (1.5 min)
6. **Open Langfuse.** One trace tree: ADK planner, three CrewAI workers, tool calls. Show the A2A agent card of a worker. (1.5 min)
7. **Eval report.** Cost per run, speedup, Jev on versus off. (1 min)

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
| What are the limits? | Cursor edits through its own tools so isolation is detected and reverted rather than prevented, Cursor usage is billed by Cursor, headless auth in a container needs verification, DeepSeek tool-calling reliability, merge conflicts are reported and not resolved, workers have network access |
