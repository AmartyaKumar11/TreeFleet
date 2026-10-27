# Requirements trace

Every row must end with an evidence path that exists before submission. Phase 11 enforces that with a test.

| # | Requirement | How TreeFleet satisfies it | Where | Proof to show | Evidence path |
|---|---|---|---|---|---|
| 1 | Context sharing and state updates | ADK `session.state` carries request, plan, routing, worker results, isolation, verdicts, report. Each stage reads only what the previous stage wrote. Workers receive a typed envelope and return a typed result | `contracts.StateKey`, `orchestrator/`, CONTRACTS section 4 | Stage events in the run log. `evidence/phase-4/state-trace.jsonl` was not produced | `evidence/phase-6/live-clean/events.jsonl` |
| 2 | Tool integration using MCP, at least two external tools | Two custom MCP servers (`worktree-fs-mcp` for workers, `gitops-mcp` for the reviewer) plus two external tools: Cursor CLI as the coding tool and Jev as an HTTP tool, plus git as a library tool | `mcp_servers/`, `jev/` | Tool-argument, tool-result and Jev routing events. `evidence/phase-5/tool-events.jsonl` was not produced | `evidence/phase-6/live-clean/events.jsonl` |
| 3 | Structured output with Pydantic | Plan, WorkerDraft, WorkerResult, ReviewVerdict, FinalReport are Pydantic models. The final output is JSON and a deterministic Markdown report with tables | `contracts.py`, `FinalReport.to_markdown` | `report.json` and `report.md` from a run | `evidence/phase-6/live-clean/report.md` |
| 4 | Suitable agentic design patterns | Orchestrator-workers, parallelization, routing, evaluator-optimizer, guardrail, human-in-the-loop hook | ARCHITECTURE section 5 | Event log shows parallel workers, a revise loop and a routing decision. `evidence/phase-6/timeline.png` was not produced | `evidence/phase-6/live-clean/events.jsonl` |
| 5 | Task monitoring and logging through callbacks | ADK before and after agent, model and tool callbacks. CrewAI task callback, guardrail and step hook. All feed one `EventSink` with JSONL and SSE. Langfuse is deferred with requirement 9 | `orchestrator/callbacks.py`, `worker/callbacks.py`, `events.py` | `events.jsonl` with intermediate outputs, execution flow and errors | `evidence/phase-6/live-clean/events.jsonl` |
| 6 | Agent to agent communication with A2A, interoperability | ADK orchestrator (A2A client) talks to CrewAI workers (A2A servers) through agent cards and `message/send`. Two frameworks, one protocol | `orchestrator/fleet.py`, `worker/a2a_server.py` | Worker exit events record `a2a: remote`. `evidence/phase-4/a2a-exchange.json` was not produced | `evidence/phase-4/events.jsonl` |
| 7 | Use both CrewAI and ADK, no other agentic framework | ADK for the orchestrator, CrewAI for the workers. LiteLLM, the A2A SDK and the MCP SDK are libraries, not agentic frameworks. Pending the professor's written confirmation | `pyproject.toml` extras, ARCHITECTURE section 4 | Dependency split is in the extras. The written confirmation is not on file yet | `pyproject.toml` |
| 8 | Secure the system with guardrails and validation callbacks | Six guard stages. Input parsing for toxic and injection content. Tool argument checks. Type checking of tool returns. Task guardrail on scope. Output redaction | `guards/`, ARCHITECTURE section 8 | Direct injection blocked in the phase 5 demo and again as eval E4. `evidence/phase-5/guard-report.md` was not produced | `evidence/phase-5/guard-demo/b/report.md`, `eval/report.md` |
| 9 | Observability for tracing, auditability, provenance | Deferred. Langfuse (phase 7) is not built, so there is no trace tree. The local audit log is `events.jsonl` | `events.py` | Deferred: phase 7 was not started | `evidence/phase-6/live-clean/events.jsonl` |
| 10 | End-to-end performance evaluation, cost and running time estimates | Eval harness with E1–E6, tokens by role, USD per run, wall time versus a sequential estimate. The 5/10/20 projection is an extrapolation | `src/treefleet/evals/`, `eval/scenarios.toml` | Per-scenario table and the Estimates section | `eval/report.md`, `evidence/phase-9/eval-report.md` |
| 11 | Deploy on a cloud platform | Deferred. Cloud deployment (phase 10) is not built, so there is no service URL, deploy log, or screenshot | not built | Deferred: phase 10 was not started | deferred: phase 10 not built |

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
