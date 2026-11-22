# 15.D6 evidence

| File | Asserts before capture |
|---|---|
| `01-palette.png` | Palette open; `#command-palette` visible; query filled; `.palette-item` count ≥ 1; bidi pill present; hit-test on `.command-panel` |
| `02-notifications.png` | `#notif-popover` open; `.notif-row` ≥ 1; unread badge visible; bidi pill in list; hit-test on `.notif-wrap` |
| `03-settings.png` | `#settings-panel` open; mode + motion controls present; light theme disabled; hit-test on panel |
| `04-board-390.png` | Viewport 390×844; no horizontal page scroll; hit-test on `.shell` |

Also recorded (not screenshots):

- `assistant-join-examples.json` — five ASSISTANT_JOIN examples from audit-slice output_lines
- `poll-tick-cost.json` — median / p95 over 50 paints at 5,000 entries
