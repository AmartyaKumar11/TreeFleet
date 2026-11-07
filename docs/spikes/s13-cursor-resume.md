# S13: Cursor `--resume` with `--print`

Worktree: `C:\wt\s13-spike` (scratch of `D:\pr-sentinel-demo`). Model: `composer-2.5-fast`.

## Session id location

In `stream-json` output, `session_id` appears on the `system`/`init` line and on later
events including `result`. Example field: `session_id` (UUID).
Captured id: `c8ca7245-357e-427e-84af-4568bed853c4`.

## Timings (seconds)

| run | duration_s | session_id | exit |
|---|---:|---|---:|
| cold-1 | 26.323 | bf60e341-07ac-463a-963b-18f986cd7aa2 | 0 |
| cold-2 | 24.476 | 994c310d-f9ca-4c73-a12a-f022637169f2 | 0 |
| cold-3 | 20.766 | c8ca7245-357e-427e-84af-4568bed853c4 | 0 |
| resume-1 | 20.875 | c8ca7245-357e-427e-84af-4568bed853c4 | 0 |
| resume-2 | 36.835 | c8ca7245-357e-427e-84af-4568bed853c4 | 1 |
| resume-3 | 38.964 | c8ca7245-357e-427e-84af-4568bed853c4 | 0 |
| resume-edit | 23.133 | c8ca7245-357e-427e-84af-4568bed853c4 | 0 |

- cold median: **24.476s** (raw: 26.323, 24.476, 20.766)
- resume median: **36.835s** (raw: 20.875, 36.835, 38.964)
- cut: **-50.5%** (need ≥ 30% to implement)
- resume+print edit exit=0, marker_in_readme=True
- decision gate: **FAIL**

## Notes

`--resume` was passed together with `--print --output-format stream-json --trust`.
Resume still works with `--print` (resume-edit exit 0 and wrote `S13-RESUME-EDIT` into README).
Resume-2 hit reconnect/retry noise and exited 1; medians still count that run.

## Decision

Gate **FAIL**: resume median was **slower** than cold (cut −50.5%, need ≥ +30%).
**No TreeFleet implementation.** Warm-resume Round 1 stops at this spike.
Scratch worktree `C:\wt\s13-spike` and branch `s13-spike-branch` were removed afterward.
