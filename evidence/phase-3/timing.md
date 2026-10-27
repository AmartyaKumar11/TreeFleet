# Phase 3 timing

Throwaway repo, one sentence added to `README.md` ("The tree is planted."). Three runs, one model each, sequential. No named checks, so there is no `check` step. Every run finished `done` with summary `README.md +1 -0`. No cursor call set `timed_out`.

| Model | Total s | Start | DeepSeek | Cursor | Commit | CLI ms | Input | Output | Cache read |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| composer-2.5-fast | 50.4 | 0.3 | 5.1 | 40.8 | 0.2 | 10186 | 27454 | 239 | 21728 |
| composer-2.5 | 38.7 | 0.1 | 3.1 | 31.1 | 0.3 | 9884 | 12563 | 276 | 36764 |
| cursor-grok-4.6-high | 43.4 | 0.1 | 3.4 | 35.9 | 0.2 | 18527 | 13489 | 794 | 39808 |

DeepSeek is three calls per run (cursor, then `git_diff`, then the notes JSON). Cache write was 0. Notes are the agent's own sentence. The summary is the git numstat line, not that sentence.

Cursor is the slow step, about four fifths of each run. The CLI reports 10–19s of model time. The rest of the 31–41s is process startup. The tails are success results, not exploration:

- composer-2.5-fast, 40.8s, `duration_ms` 10186: added "The tree is planted." as a new line after `base`.
- composer-2.5, 31.1s, `duration_ms` 9884: same edit, existing `base` line unchanged.
- cursor-grok-4.6-high, 35.9s, `duration_ms` 18527: "I'll append that sentence to README.md only, without changing anything else."

Direct `cursor-agent --print --output-format json --model composer-2.5-fast --workspace <dir>` without `--trust` exited 1 in 1.3s on the Workspace Trust prompt. The same call with `--trust` and `--sandbox disabled` (what the worker uses on Windows) took 75.7s wall, CLI `duration_ms` 17221, exit 0, and wrote the sentence. That wall time is a cold start. The three worker calls above were faster.

`Limits.timeout_s` is 180. That is a little over twice the 76s direct call and about four times the slowest worker cursor step. The old default of 900 only mattered when the model kept launching cursor.
