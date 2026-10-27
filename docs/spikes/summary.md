# Phase 0 spike summary

Python 3.12.16 via uv 0.13.6. Orchestrator extra in `.venv-orch`, worker extra in `.venv-worker`.

- S1 Result: FALLBACK drop output_schema, parse JSON with Pydantic and retry with the validation error
- S2 Result: PASS
- S3 Result: FALLBACK Serve through an A2A SDK app whose executor runs the crew
- S4 Result: PASS
- S5 Result: PASS
- S6 Result: DEFERRED (no Langfuse keys, so the spike was not run)
- S7 Result: PASS
- S8 Result: PASS
- S9 Result: FALLBACK LiteLLM mock_response or a recorded-fixture proxy; the shim is a small Python script on PATH
- S10 Result: FALLBACK hard timeout and process-tree kill; scope diff and proofs instead of permission file
- S10b Result: FALLBACK sandbox unavailable on Windows
- S11 Result: FALLBACK run the demo locally

Model ids from DeepSeek: `deepseek-flash`, `deepseek-v4-pro`.

`cursor-agent` 2026.10.22-27dcf5f. Print mode is `--print`, output is `--output-format json`, model is `--model`, workspace is `--workspace` with cwd set to the worktree. With `--sandbox disabled`, a per-worktree permission file blocked neither a denied file nor an outside write. `--sandbox enabled` does not start on Windows.

`RemoteA2aAgent` needs `a2a-sdk` 0.3.26. The orchestrator extra does not install it. 1.2.3 is a different API from the worker's 0.3.26.
