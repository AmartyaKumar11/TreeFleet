# Run aa85638e

Goal: add unit tests for `src/notifications.py` and, as a second task, for `src/admin.py`. Finished in 680 seconds. Notifications was approved and merged into `tf/aa85638e/integrated`. Admin was rejected. `demo-base` stayed at `3be485b`.

## Why the admin revise did not change the file

The reviewer asked for a revise at 02:50:03. The feedback in the verdict was: the tests only check that `check_permissions` is called, so a bug that ignores its return value would still pass, and token tests cover empty and expired tokens only.

Cursor then ran twice on `add-admin-unit-tests`. Neither run changed the tree. `HEAD` stayed `56bd20d` (`tests/test_admin.py`, +94). The worktree was clean afterward. `reverted_scope` on the result is empty, and the event log has no scope guard during the revise. The one `guard.decision` in that window is a tool-result guard at 02:51:59, after both Cursor runs had already finished (02:50:47 and 02:51:36). A scope guard did not revert the file.

Both runs called `edit` on `tests/test_admin.py` and the tool finish was `ok: true`. Three `shell` finishes were `ok: false` (Cursor could not run pytest). The final messages restated the original file:

First call: "Creating `tests/test_admin.py` following existing test patterns" and "Writing the file to satisfy the create-only requirement." It describes `dashboard` and `audit_log` coverage that was already in the +94 file. It does not mention the return value of `check_permissions` or a malformed token.

Second call: "`tests/test_admin.py` is on disk (94 lines)." Same coverage list. It tells the reader to run pytest locally. It does not apply the reviewer's required changes.

The worker prompt still leads with the original instruction, "Create tests/test_admin.py", then appends `Revision:` and `cursor_instruction` appends `Required changes:`. Cursor followed the create-the-file framing and wrote the same 94 lines again. That is Cursor's judgment, steered by that prompt. It is not a scope revert.

The revise result was then failed before a second commit. The closing notes were not valid JSON because a control character was inside the string (`WorkerDraft` `json_invalid`, input starting `{"notes": "BLOCKED —`). `git status` was clean, so the worker returned that parse error and dropped Cursor's final message. The review compared `git diff base...branch`, saw the same patch, and recorded `no new diff`.

## The tool-result guard hits

The log has six `guard.decision` events with `reasons: ["malformed tool result"]`. Five are from the first pass: notifications at 02:43:59, 02:45:09, 02:45:26, and 02:45:28, and admin at 02:46:49. The sixth is the admin revise at 02:51:59. The event stores only that reason. The worker logs store `CallToolRequest` and not the result body. The guard replaces the body before the model sees it, so the run does not contain the raw string.

The same CrewAI client against `C:\wt\aa85638e\add-admin-unit-tests` returns two shapes.

A normal tool result is JSON and already passes the guard:

```json
{
  "ok": true,
  "data": { "lines": [] },
  "error": null
}
```

`read_file` of `tests/test_admin.py` and `search` are the same shape with `"ok": true`. `run_check` of an unknown name is `{"ok": false, "error": {"code": "NOT_ALLOWED", "message": "nope"}}`, which also has `ok` and is kept.

The shape the guard drops is a plain-text FastMCP validation error, which is still a real tool result (`isError` text). `json.loads` fails, so the guard substitutes a generic `BAD_ARGS` / `malformed tool result` and the validation text is lost:

```text
Error executing tool read_file: 1 validation error for read_fileArguments
path
  Field required [type=missing, input_value={}, input_type=dict]
```

```text
Error executing tool git_diff: 1 validation error for git_diffArguments
path
  Input should be a valid string [type=string_type, input_value=['tests/test_admin.py'], input_type=list]
```

Most `CallToolRequest` lines in the worker logs have no matching guard, which matches the JSON shape being accepted. The six hits are the non-JSON error text.
