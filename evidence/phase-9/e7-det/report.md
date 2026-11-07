# TreeFleet run 0bf210cd

**Goal:** Deterministic same-hunk conflict for integration feedback.

## Tasks

| Task | Status | Verdict | Revise rounds | Branch | Files | Checks |
|---|---|---|---|---|---|---|
| alpha | done | approve | 0 | `tf/0bf210cd/alpha` | 1 | none |
| beta | done | approve | 0 | `tf/0bf210cd/beta` | 1 | none |

## Isolation proof

**Overall:** PASS

| Task | Process cwd | Toplevel | Registered | Canary absent | Within scope |
|---|---|---|---|---|---|
| alpha | yes | yes | yes | yes | yes |
| beta | yes | yes | yes | yes | yes |

Main checkout unchanged: yes; canary still present: yes

## Integration

Branch: `tf/0bf210cd/integrated`
Merged: alpha, beta
Post-merge checks: pytest:pass, npm-test:pass, test:pass
Merge into `demo-base`: `git -C D:\pr-sentinel-demo merge tf/0bf210cd/integrated`

## Notes

- deterministic conflict resolve calls: beta
- integrate 37.2s

## Metrics

- Wall time: 38.0s (end-to-end clock, including planning)
- Sequential time: 37.2s (pre-fleet overhead plus task, review and integration time)
- Speedup: 0.98x (sequential time divided by wall time)
- Fleet wall: 0.0s (time while workers run)
- Fleet speedup: 0.0x (sum of task durations divided by fleet wall)
- Review time: 0.0s
- Integration time: 37.2s
- Total cost: $0.0000
- Jev calls: 0
