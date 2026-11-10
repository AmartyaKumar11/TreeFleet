# Contributing

TreeFleet is pre-1.0. Contributions should stay small, tested, and aligned with [`AGENTS.md`](AGENTS.md).

## Before you open a pull request

1. Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/CONTRACTS.md`](docs/CONTRACTS.md).
2. Install both virtual environments (see [`README.md`](README.md) Quickstart).
3. Run the gate for the phase you touched: `python scripts/gate.py --phase N`.
4. If you change a contract, add or update tests and note it in [`docs/STATUS.md`](docs/STATUS.md).

## Conventions

- Python 3.12, full type hints, `from __future__ import annotations`.
- No new agentic frameworks beyond Google ADK and CrewAI.
- Do not weaken tests to get green; fix the cause.
- Never commit secrets, local run directories, or `.venv*` trees.

## Questions

Open a GitHub issue with your environment (OS, Python version) and the gate command output (redact secrets).
