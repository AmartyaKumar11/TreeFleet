# S12 cursor-agent stream-json

`cursor-agent --help` lists `--output-format stream-json` and `--stream-partial-output`. Both are supported.

`s12-cursor-stream.sample.txt` is one real `composer-2.5` run that added a line to a README. Exit 0. 58 lines.

The parser keeps:

- `assistant` deltas. They have `timestamp_ms` and no `model_call_id`. The text is `message.content[].text`.
- `tool_call` with `subtype` `started` or `completed`. The tool object has one key ending in `ToolCall`. The path is `args.path`. A completed call is successful when `result` contains `success`.
- `result`. `result` is the final message. `usage` uses `inputTokens`, `outputTokens`, `cacheReadTokens`, and `cacheWriteTokens`.

It drops `system`, `user`, and `thinking`. An assistant line with `model_call_id` repeats the deltas already seen. The assistant line with no timestamp repeats `result.result`. A bare `{"usage": ...}` line is the offline shim, not this CLI.
