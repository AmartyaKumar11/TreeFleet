# TreeFleet design direction (locked)

Subject: a developer tool for supervising parallel coding agents. Audience: Hemang and a professor reviewing isolation. Primary job: show several workers editing in separate worktrees and prove, with evidence, that they stayed apart.

References: AO's own app (board of worker sessions in lanes, "Needs you" first, each task with its own branch and worktree, feedback routed to the owning worker) and Cursor's dark editor feel (quiet neutral surfaces, thin borders, no decoration). Dark only.

## Decisions

| Topic | Decision |
|---|---|
| Theme | Dark only |
| Start | Open a folder, type the goal in one prompt box, press Run. Checks are auto-detected and confirmed once |
| Plan approval | On by default. A "Needs you" card shows the plan and scopes, and nothing spawns until Approve plan. A header switch, "Auto-run without plan approval", turns it off |
| Live output | Read-only stream of each worker's Cursor run. No terminal you can type into |
| Failed tasks | No separate lane. They sit in Needs you with a red marker |
| Memorable element | The branch map: main as a trunk, one line per worktree, proof state in the line color |

## Tokens

| Token | Value | Use |
|---|---|---|
| ground | `#121314` | Page |
| surface | `#17181a` | Cards, prompt, drawer |
| raised | `#1e1f22` | Buttons, diff headers |
| line / line-strong | `#2a2b2f` / `#3a3b40` | Borders. No shadows |
| text / muted / faint | `#e6e6e3` / `#8b8c92` / `#5d5e64` | Text levels |
| green | `#4cc38a` | Proof passed, approved, ready |
| red | `#f0616d` | Failed, scope violation, proof failed |
| yellow | `#e5b94a` | Needs attention, review, revision, waiting |
| blue | `#5aa2f0` | Working, selected, focus |

The four colors are small indicators only: a dot, a proof segment, a branch line, a log line. Never a large fill. Every indicator is paired with words, so color is never the only signal.

Type: Schibsted Grotesk for the interface (400, 500, 600). JetBrains Mono only for real machine data: branch names, paths, commit ids, log lines, diffs. Sentence case everywhere. No all-caps labels, no eyebrows.

Shape: cards 6 px radius, chips 3 px, buttons 5 px, prompt 8 px. Borders carry structure. Sizes: base 14 px, mono 12 px.

## Screens

1. **New run.** Header with folder path, "Change folder", base ref pill, main-checkout state, and the auto-run switch. Prompt box with detected checks as chips and "Edit checks". Beside Run, a Worker model select. The first option is Smart routing (stored value `router`). The second is Cursor Auto (stored value `auto`, Cursor's own model). Further options are the other discovered Cursor models. Smart routing means TreeFleet chooses per task.
2. **Plan approval.** A card in Needs you listing tasks with their scopes. Each task has its own model select, defaulting to Smart routing, which sets `TaskSpec.model_override` when changed. Buttons: Approve plan, Edit.
3. **Board.** Lanes: Needs you, Working, In review, Ready to merge, and a collapsed Merged row. Each card shows title, branch, a status line with a colored dot, a five-segment proof strip, elapsed time and file count.
4. **Task drawer** (right side, opens on card click). The header shows the Cursor model chosen for that task, next to the branch. Tabs: Live output, Diff, Proof, Feedback. Feedback goes only to that worker as the next revision, with the count shown ("revision 1 of 2").
5. **Branch map** under the board. Trunk labeled with the main checkout's commit. One colored line per worktree labeled with branch and worktree path, ending in "N of 5 proofs". Header pills: "Main checkout unchanged", "Canary absent in every worktree".
6. **Final report.** The `FinalReport` rendered: plan, results, proofs, verdicts, merges and conflicts, cost lines, wall time against sequential time, and a download of patches.

Empty state: "Pick a folder and describe what to build." Errors say what failed and what to do, for example "pytest is not installed in this folder. Edit checks to use a different command."

## Motion

One moment: when workers spawn, the branch lines draw out from the trunk once. Everything else responds to an action (tab switch, toggle, card selection). `prefers-reduced-motion` disables the draw.

## Responsive and accessibility

Desktop first. Under 1100 px the drawer moves below the board. Under 900 px lanes become two columns, under 560 px one. Visible keyboard focus on every control, tabs use `role="tablist"`, the switch uses `role="switch"`, contrast passes on the dark surfaces.

## Changes made after reviewing the first plan against generic defaults

- Lane headers were going to be uppercase with tracked letters. Now sentence case.
- Small labels were going to be monospace everywhere. Mono is now reserved for real paths, branches and logs.
- Cards were going to carry shadows and one shared radius. Now borders only and a radius per element type.
- A separate Failed lane was dropped to keep AO's four-lane shape.

## Consequences for the build

- `RunRequest` gains `plan_approval: bool = True`. The run pauses after the plan with an `awaiting_approval` event and resumes on `POST /api/runs/{id}/approve`.
- Folder browsing: `GET /api/fs/list?path=` on the local server, bound to `127.0.0.1`, directories only, bearer token required.
- Checks are detected by the repo scan from `pyproject.toml`, `package.json` or a `Makefile`, shown for confirmation, and only confirmed commands become trusted `checks`.
- Live output needs a new `output_line` event kind carrying a truncated, secret-redacted line of Cursor's output. This relaxes rule 5 in `docs/CONTRACTS.md` (workers must not put file contents in `ProgressEvent.data`): `output_line` is local-only, capped at 200 characters per line, and redacted by the same filter as final output. Make this a contract change with a test in Phase 3.
- The CLI is folder-first: run `treefleet "goal"` inside the project folder. `treefleet "goal" --model router|<id>` sets the worker model. The default is `router`. `auto` is Cursor's own model id and is stored unchanged. `treefleet models` lists discovered ids and labels.
- Worker model fields are already on the contract: `RunRequest.worker_model`, `TaskSpec.model_override`, `WorkerTaskEnvelope.cursor_model`. The mock lists Smart routing, Cursor Auto, then `composer-2.5-fast`, `composer-2.5`, and `cursor-grok-4.6-high` from the 2026-10-27 model list.
- A static, clickable mock is at `docs/mockup/index.html`. Phase 8 must match it.
