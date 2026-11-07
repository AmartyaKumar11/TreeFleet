# E7 diagnosis (run `08396b8f`)

Sources: `runs/08396b8f/events.jsonl`, `runs/08396b8f/report.json`, live stderr from the eval process.

## Why beta looked like “no change”

Not a planner dependency (forced plan, no `depends_on`). Not a scope guard (no revert/scope events for beta).

Timeline on `beta`:

1. Cursor round 1 (~28s) **did** edit `README.md` (`cursor.tool_finish` edit ok). Final Cursor text: the “Small Python app” sentence is now “Beta owns…”.
2. Three further DeepSeek worker-brain steps (~47s, ~47s, ~33s), then Cursor round 2 (~25s). Cursor then reported the file **already** had the Beta wording, so it made no second edit.
3. Unlike `alpha`, beta never emitted a `worker.step` `commit`. The orchestrator recorded `worker.exited` with:

   - `status=failed`
   - `files=[]`
   - `error=RemoteA2aAgent fallback: RuntimeError; RuntimeError: A2A client returned no WorkerResult`

So the empty diff in the final `WorkerResult` is **A2A losing the result** after Cursor had already edited, not “worker made no changes” and not “Cursor refused.” The disk edit may still have existed in the worktree; the returned artifact did not carry it.

`alpha` finished `done` with `README.md` +1/−1. Its Cursor message also said the Alpha sentence was already present (second look after the first edit). That is normal Cursor narrative, not a planner bug.

## InternalServerError (reviewer)

Class: `litellm.exceptions.InternalServerError` (wrapped DeepSeek / OpenAI-shaped 500).

Redacted message shape (request id kept; no secrets in body):

```
InternalServerError: DeepseekException - {"error":{"message":"Internal Server Error (request_id: cc49cb67-52b0-46b5-b5f4-e289cba30fd8)","type":"internal_error","param":null,"code":"internal_error"}}
```

HTTP meaning: provider 5xx / `internal_error`. Transient.

## Path from provider error to `reject`

1. `ReviewerAgent` → `review_loop` → `_review_one` → `_live_verdict` → `_complete(LiteLlm…)` for the reviewer.
2. LiteLLM raises `InternalServerError`.
3. `review_loop` catches **any** `Exception` around `review(...)` and calls `_forced_reject(task_id, type(exc).__name__)`, which builds `verdict=reject` with reason `InternalServerError` only (exception body discarded).
4. Events: `review_verdict` with `verdict=reject`, `reason=InternalServerError` for both `alpha` and `beta`.
5. Integrator then excludes both as `rejected: InternalServerError`. No conflict resolve ran.

Same pattern would reject on any transient provider failure. That is the bug to fix next.
