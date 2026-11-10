# Security

## Reporting vulnerabilities

If you believe you found a security issue, **do not** open a public GitHub issue with exploit details.

Email the maintainers through GitHub private reporting (Security → Advisories) or contact the repository owner with:

- A description of the impact
- Steps to reproduce
- Whether secrets or untrusted PR comment content is involved

## Scope

TreeFleet is designed for **local, single-operator** use: loopback UI, explicit git worktrees, and optional `gh` integration. Treat these as trust boundaries:

- **Untrusted PR comments and diffs** — wrapped and guarded before they reach workers (see [`docs/PR_LIFECYCLE.md`](docs/PR_LIFECYCLE.md)).
- **Worker processes** — minimal environment; isolation proofs after each wave.
- **Secrets** — only via environment variables (`DEEPSEEK_API_KEY`, `TYPESAFE_API_KEY`, optional `CURSOR_API_KEY`). Never log values.

## Out of scope for this repository

Hosted multi-tenant deployment, unauthenticated remote control plane, and sandbox-enforced Cursor on Windows are not security guarantees today. See README Limitations.
