# Contributing

TreeFleet is pre-1.0. Contributions should stay small, tested, and aligned with [`AGENTS.md`](AGENTS.md).

## Before you open a pull request

1. Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and [`docs/CONTRACTS.md`](docs/CONTRACTS.md).
2. Install both virtual environments (see [`README.md`](README.md) Quickstart).
3. One-time: install Chromium for UI tests (`playwright install chromium` in `.venv-orch`).
4. Run the gate for the phase you touched: `python scripts/gate.py --phase N` (~4–8 min for phase 11).
5. If you change a contract, add or update tests and note it in [`docs/STATUS.md`](docs/STATUS.md).

Without browsers, contributors can still run the non-UI suite (~1–2 min):

```bash
pytest -m "not ui"
```

The full gate requires Chromium: `scripts/gate.py` sets `TREEFLEET_REQUIRE_UI=1` so missing browsers fail loudly (`run: playwright install chromium`) instead of skipping UI tests.

## Conventions

- Python 3.12, full type hints, `from __future__ import annotations`.
- No new agentic frameworks beyond Google ADK and CrewAI.
- Do not weaken tests to get green; fix the cause.
- Never commit secrets, local run directories, or `.venv*` trees.

## Evidence screenshots

Playwright tests write screenshots through `tests/_evidence.evidence_path`. By default those files land in a pytest temp directory so a gate run does not dirty `evidence/`. Set `TREEFLEET_WRITE_EVIDENCE=1` only when you intentionally want captures under the real `evidence/` tree (for example refreshing demo screenshots).

## Questions

Open a GitHub issue with your environment (OS, Python version) and the gate command output (redact secrets).
