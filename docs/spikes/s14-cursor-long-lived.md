# S14: Cursor long-lived / multi-instruction process modes

Budget: 15 minutes. CLI: `cursor-agent` 2026.11.05-86047df on Windows (`win32`/`x64`). No TreeFleet code changes.

## Method

1. `cursor-agent --help` — full option and command list.
2. `cursor-agent <command> --help` for every listed command: `persist`, `login`, `logout`, `mcp`, `plugin`, `worker`, `status`, `models`, `team`, `bedrock`, `about`, `update`, `create-chat`, `generate-rule`, `agent`, `ls`, `resume`, `help`.
3. Spot probes: `persist list`, `create-chat`, `ls` (non-TTY), `worker debug`, string search of the shipped `index.js` for ACP / tmux / JSON-RPC.

## Candidates that look “long-lived”

| Mode | What help says | One process, many instructions? | Headless on Windows? | `--trust` / `--model` |
|---|---|---|---|---|
| Default interactive agent (no `--print`) | Starts Cursor Agent TUI | Interactive REPL-style chat in one process | **No** for automation: Ink raw-mode TTY required; `ls`/`resume` fail with “Raw mode is not supported on the current process.stdin” when stdin is not a TTY | Global flags include `--trust` and `--model` on the root command |
| `persist` / `persist attach` / `persist stop` | “Session that survives terminal and SSH disconnects” (tmux-backed) | Yes on supported OS: detach/attach to one session | **No** — `persist list` prints: `Persistent tmux sessions are supported only on Linux and macOS.` | Root flags are not documented on `persist --help`; not usable on this machine |
| `worker` / `worker start` | Self-hosted Cloud Agent worker; stays connected; runs claimed cloud tool calls | Process stays up, but instructions arrive from **Cursor Cloud claims**, not from local stdin/socket follow-ups for TreeFleet | **Partially** — Windows supported with Git for Windows / Git Bash (`worker debug` ran). Not a local multi-prompt API | Worker has its own flags; not the same as `--print --trust --model` coding turns |
| `--print` + `--resume` / `create-chat` + `--resume` / `resume` / `ls` | Resume a **chat id** across **new** process launches | **No** — each invocation is a new OS process (confirmed S13; resume was not faster) | Yes with `--print` | Yes (`--trust`, `--model` on root) |
| ACP / JSON-RPC host | Bundle contains the string `Agent Client Protocol` and `JSON-RPC`; **no** CLI command or `--acp` / stdio server mode in `--help` | Not exposed as a documented agent host for local multi-turn scripting | N/A — no CLI surface found | N/A |

## Modes that are not long-lived local hosts

Auth/config utilities only: `login`, `logout`, `status`, `models`, `team`, `bedrock`, `about`, `update`, `mcp`, `plugin`, `generate-rule`.

`agent` subcommand help only shows `[prompt...]` and `-h`; it does not advertise a server/ACP mode.

## Timing

**Skipped.** No mode on this Windows CLI keeps **one local process** that accepts **several scripted follow-up instructions** over stdin or a socket in a headless way. The only true multi-turn process options are:

- interactive TUI (not headless),
- `persist` (not on Windows),
- `worker` (cloud claim host, wrong control plane for TreeFleet).

No three-instruction-vs-three-cold-`--print` comparison was run.

## Decision for TreeFleet

Do **not** implement a long-lived Cursor process wrapper. Continue with cold `--print` (or other non-Cursor levers from `docs/AO_PARITY_GAP.md`). Revisit only if Cursor ships a documented Windows headless session host (ACP stdio or similar) with `--trust` / `--model`.
