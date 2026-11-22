# 15.D5b evidence asserts

| File | Asserts before/after capture |
|---|---|
| `01-summary.png` | `.transcript-mode [data-mode=summary][aria-checked=true]`; no `assistant_message`/`tool_*` kinds; locator visible + hit-test via bounding box |
| `02-normal-tool-group.png` | Normal mode; collapsed tool-group `aria-expanded=false` (or thread fallback); visible |
| `03-verbose-tool.png` | Verbose mode; tool_call row visible |
| `04-plan-approval-pending.png` | `.transcript-plan-card` visible; Approve button present |
| `05-loading-skeleton.png` | `.transcript-skeleton` visible; luminance delta vs pane > 0.02 |
| `06-bidi-placeholder.png` | Transcript message/thread shows bidi pill or `U+202E` placeholder; safe-text walk found no raw controls |

Screenshots via `locator.screenshot` only.
