# Evidence index

One line per folder. Paths are relative to `evidence/`.

| Folder | What it proves |
|---|---|
| `phase-1/` | Phase 1 gate green: skeleton, config, events, contracts |
| `phase-2/` | Phase 2 gate green: worktrees, canary, isolation proofs |
| `phase-3/` | Phase 3 gate green: CrewAI worker, MCP, A2A server (plus live timing sample) |
| `phase-4/` | Phase 4 gate and first orchestrator live run (events, report, timing, diagnosis) |
| `phase-4/live-2/` | Second live run: isolation PASS after no-commit upstream fix |
| `phase-4/live-3/` | Live timing / report sample for orchestrator iteration |
| `phase-4/live-4/` | Three-task wave live run: checks passed, isolation PASS, speedup measured |
| `phase-5/` | Phase 5 gate green: guards, Jev, callbacks, routing |
| `phase-5/guard-demo/a/` | Guard demo A: completed README edit with Jev routing |
| `phase-5/guard-demo/b/` | Guard demo B: injection goal blocked at the door, no new worktree |
| `phase-5/guard-demo/c/` | Guard demo C: forced `../x` plan rejected by the plan guard |
| `phase-6/` | Phase 6 gate green: review loop and integration |
| `phase-6/live-clean/` | Clean three-task live run: all approved, integrated branch, isolation PASS |
| `phase-6/live-saboteur/` | Saboteur live run: one task rejected, others approved, main checkout clean |
| `phase-8/` | Phase 8 lite board: preflight, stage screenshots, dry-run events/report, demo video |
| `phase-8/live-2/` | Second live board capture: mid-fleet lanes/worktrees/output, done screen, webm |
| `phase-9/` | Phase 9 gate green and eval report (E1–E6 cost, time, expectations) |

No `phase-7/` or `phase-10/` folders: observability and cloud deploy are deferred (see `docs/REQUIREMENTS_TRACE.md` requirements 9 and 11).
