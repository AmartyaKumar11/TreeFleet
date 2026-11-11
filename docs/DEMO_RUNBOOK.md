# Demo runbook

Path-neutral instructions for the public PR-mode demo. Machine-specific notes in `docs/DEMO.md` are superseded by this file.

## Prerequisites

- TreeFleet checkout with `.venv-orch` and `.venv-worker` installed (see README Quickstart).
- `gh` authenticated to a login that can open PRs on the demo repository.
- Environment secrets only (never paste keys into config): `DEEPSEEK_API_KEY`, optional `TYPESAFE_API_KEY`, Cursor credential as used by the worker.
- Demo git repo at `<demo-repo>` (clone of `AmartyaKumar11/pr-sentinel-demo` or your fork).
- Board: `treefleet ui --repo <demo-repo>` (loopback only).

## Demo repository branches (verified names and SHAs)

| Branch | SHA | Role |
|---|---|---|
| `demo-base` | `c968688336d044a637bea4ef4608bb5833fbe710` | Original demo baseline |
| `demo-prof` | `e92286b0ee8d722a9b28eb77e66cb89c6cf3bb43` | Real-demo PR base (pytest on PRs only) |
| `demo-rehearsal` | `e92286b0ee8d722a9b28eb77e66cb89c6cf3bb43` | Rehearsal PR base (same tip as `demo-prof`) |

Verify without changing anything:

```
git -C <demo-repo> rev-parse demo-base demo-prof demo-rehearsal
```

`demo-prof` must remain at `e92286b0ee8d722a9b28eb77e66cb89c6cf3bb43` for the frozen rehearsal.

## Config switch

Do not rely on a gitignored local demo file. Copy the placeholder and fill values:

```
cp docs/examples/treefleet.demo.example.toml <path-to>/treefleet.demo.toml
```

Point TreeFleet at it:

```
set TREEFLEET_CONFIG=<path-to>/treefleet.demo.toml   # Windows cmd
# export TREEFLEET_CONFIG=<path-to>/treefleet.demo.toml  # Unix
```

In that file:

- Real demo: `[pr] base = "demo-prof"`
- Rehearsal: `[pr] base = "demo-rehearsal"`
- `repo` and `trusted_commenters` must be set explicitly; there is no shipped demo default for `base` when `[pr] enabled = true`.

TreeFleet freeze for the recorded rehearsal run `aa68e041` is local tag `demo-freeze-1` → `8b07d50464f7bbecc8d0e8e6ee3d6e3ac534d036`.

## Run order

1. Confirm branches/SHAs above.
2. Set `TREEFLEET_CONFIG` to the demo config (`base = demo-prof` or `demo-rehearsal`).
3. `treefleet doctor` (fix missing keys / cursor login before going live).
4. Open the board: `treefleet ui --repo <demo-repo>`.
5. Start the two-task goal (notifications + admin unit tests). Keep plan approval on unless you intentionally auto-approve.
6. After both PRs are green, post the trusted comment (next section) on the PR that needs a human revision signal.
7. Wait for revision push, re-green checks, and merges if `auto_merge` is on.
8. Clean worktrees (below). Do not run demo reset.

## Trusted comment

Post from a login listed in `[pr].trusted_commenters`. Treat the comment body as ordinary text for humans; TreeFleet classifies it, it is not shell.

Expected pattern from rehearsal `aa68e041`: one trusted comment on PR #93 drove a single revision round; PR #92 had no trusted comment and stayed green until merge.

## Expected timings (run `aa68e041`)

Source: `evidence/phase-13/freeze/aa68e041-record.md`.

| Milestone | Elapsed |
|---|---:|
| First `pr.opened` | 343.3 s |
| Both initial `pr.checks_green` | 370.2 s |
| Trusted comment → revision push (PR #93) | comment 577.0 s; push 614.9 s |
| Both `pr.merged` / run done | 746.0 s |

## Worktree cleanup

Remove TreeFleet worktrees without force:

```
git -C <demo-repo> worktree list
git -C <demo-repo> worktree remove <worktree-path>
```

Do not pass `--force`. If remove fails, stop and inspect locks/processes.

## Never-do list

- Do not read or change PRs **#80** or **#77**.
- Keep webhook **684748816** inactive.
- Do not run `treefleet demo reset` (or any hard reset / `git clean`) on the demo repo.
- Do not force-push. Do not merge PRs unless the operator explicitly asks.
- Live tests only against `AmartyaKumar11/pr-sentinel-demo` (or the configured demo remote), never against unrelated repos.
