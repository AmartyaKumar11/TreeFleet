# TreeFleet board: UI design spec (Claude-style, dark)

Reference direction: Claude's app. Warm neutral surfaces, one terracotta accent, serif for reading and display, sans for chrome, mono for code, hairline borders, flat cards, near-zero shadow, generous spacing, calm motion. No Anthropic logos, fonts or assets are used; the look is reproduced with original work and OFL fonts.

## 0. Principles
1. Calm over busy. Whitespace and hairlines separate things, not boxes inside boxes. Never nest more than two bordered containers.
2. One accent. Terracotta marks the primary action, the running state and the selection. Nothing else is terracotta. No gradients, no glows, no colored shadows.
3. Type does the hierarchy. Size, weight and the serif/sans/mono split carry importance, not color.
4. Everything is on the 4 px grid. Row heights, paddings, gaps and radii come from tokens.
5. Dense where it is data (diffs, logs, tables), roomy where it is reading or deciding (plans, verdicts, empty states).
6. State is never color-only. Pair every color with a label, icon or position.
7. Motion explains change and never decorates. Under prefers-reduced-motion only opacity changes.

## 1. Typography
Families: sans = Geist (UI chrome, labels, buttons, tables). Serif = Source Serif 4 (page titles, orchestrator messages and goal text, empty-state headlines, settings section titles). Mono = Geist Mono (paths, branches, shas, diffs, logs, kbd, code).
Rules: never serif inside controls, tables or the diff. Numbers in tables, timers, counts and diff gutters use font-variant-numeric: tabular-nums. Body letter-spacing 0. Uppercase micro labels +0.04em.

Scale (size px / weight / line-height):
- display (serif): 28 / 400 / 1.2, tracking -0.2px (empty-state hero, onboarding)
- title-lg (serif): 22 / 400 / 1.25 (project title in the topbar, drawer title)
- title (sans): 16 / 600 / 1.35 (card titles in modals, section titles)
- title-sm (sans): 14 / 600 / 1.4 (task card titles, lane labels)
- body-lg (serif): 16 / 400 / 1.6 (orchestrator messages, goal text, plan text)
- body (sans): 14 / 400 / 1.5 (default UI text)
- body-sm (sans): 13 / 400 / 1.45 (secondary text, table cells, chips)
- caption (sans): 12 / 400 / 1.4 (hints, timestamps, helper text)
- micro (sans, uppercase): 11 / 500 / 1.3, +0.04em (section labels in the sidebar, table headers)
- code (mono): 12.5 / 400 / 1.6 (diff, logs, proof output)
- code-sm (mono): 12 / 400 / 1.4 (branch names, shas, chips)
- kbd (mono): 11 / 500 / 1

## 2. Spacing and layout
Base unit 4 px. Scale tokens: 4, 8, 12, 16, 20, 24, 32, 40, 48, 64.
Layout: sidebar 248 px (fixed). Topbar 56 px. Main content padding 24 px, max content width none (board fills), reading width for transcript messages 720 px centered. Drawer default 480 px, resizable 360 to 720 px (drag handle on its left edge, remembered per browser in localStorage with try/catch), below 1024 px viewport the drawer overlays the board with the scrim, below 640 px it becomes a bottom sheet at 88% height. Kanban lanes: min 264 px, flex equal, 16 px gap, horizontal scroll below that.
Row heights: dense controls 32 px, default controls 36 px, list rows 36 px, table rows 32 px, large composer controls 40 px. Touch pointers (pointer: coarse) raise every interactive target to 44 px.

## 3. Shape, borders, elevation
Radius tokens: 4 (kbd, tiny chips), 6 (inputs, selects), 8 (buttons, tabs, segmented control, rectangular chips), 12 (cards, popovers, composer), 16 (modals, command palette, sheets), 9999 (status badges and count pills only).
Borders: 1 px hairlines from tokens: --border (default), --border-soft (dividers inside a surface, about half strength), --border-strong (hover, input). Cards are flat: surface color plus hairline, no shadow. Hover on a card darkens the border only. Shadows only on floating layers: popover and menus --shadow-md, command palette and modals --shadow-lg, toasts --shadow-md. Scrim --overlay.
Selection: a selected card or row gets a 1 px accent border and an accent tint background (accent at 8 percent over the surface). No glow.

## 4. Color roles (tokens from 15.C and 15.C2)
- Page background: --bg (darkest). Sidebar: --sidebar. Cards and composer: --surface. Popovers, menus, raised rows: --surface-2.
- Text: --text primary, --muted secondary, --muted-2 for disabled and placeholders (must still pass AA for placeholder text at 4.5 or darker-but-labelled).
- Accent: --accent for running dots, active tab underline, focus ring, links on hover, selected borders. --accent-strong for primary button fill (white text).
- Status: --status-running (accent), --status-success (green), --status-warning (yellow), --status-error (red), --status-needs (violet). Status is always paired with a label or icon.
- Diff: added = success at 12 percent background with a 2 px success bar on the gutter; removed = error at 12 percent with a 2 px error bar; word-level change inside a line = the same color at 28 percent.

## 5. Motion
Durations 100 ms (hover, press), 150 ms (fades, color), 240 ms (panels, drawer, expand/collapse), 360 ms (page-level). Easing standard cubic-bezier(0.4, 0, 0.2, 1); entrance cubic-bezier(0.2, 0, 0, 1). Drawer slides 24 px plus fade. Expand/collapse animates height with 240 ms. Spinner 800 ms linear, only for work in progress. Skeleton shimmer 1.4 s. No bouncing, no parallax. prefers-reduced-motion: no translate, scale or shimmer; opacity only.

## 6. Icons
One set (Lucide, stroke 1.5, 16 and 20 px, currentColor, round caps), inline SVG sprite. Icon plus label in buttons uses an 8 px gap. Icon-only buttons are 32 px square with a tooltip and aria-label.

## 7. Base controls
- Primary button: --accent-strong fill, white text, body-sm 500, height 32 (36 in the composer, 44 on touch), radius 8, padding 0 14, hover one step lighter, active one step darker, disabled 40 percent opacity with not-allowed cursor, loading shows a 14 px spinner and keeps width.
- Secondary button: transparent, 1 px --border-strong, text --text, hover --surface-2 background.
- Ghost button: no border, text --muted, hover --surface-2 and text --text.
- Danger variant: error text and border, error tint on hover. Used only for cancel and reject.
- Icon button: 32 px square, ghost style, radius 8.
- Text input and textarea: --surface background, 1 px --border-strong, radius 6, height 36, padding 0 12, placeholder --muted-2. Focus: border --accent plus 2 px --ring outline offset 2. Error: border --status-error plus caption error text below. Textarea autosizes 1 to 8 rows.
- Select: same as input with a 16 px chevron; the popup uses the popover style, 36 px rows, a check on the selected row.
- Segmented control (Kanban/Orchestrator): 32 px high container radius 8 with 2 px inner padding, --surface-2 background; the active segment is --surface with a hairline and --text, inactive is --muted. Arrow keys move between segments.
- Tabs (drawer): 36 px high, no boxes, 16 px horizontal gap, label body-sm 500, active label --text with a 2 px --accent underline that slides 150 ms, inactive --muted, counts as caption pills.
- Chips: radius 8 rectangular for metadata (model, branch), code-sm, --surface-2 background, no border, 24 px high. Status badges: pill radius, 22 px high, caption 500, tinted background (status color at 14 percent) with status-colored text and a 6 px dot or icon, always with a label.
- Count pill: pill, 20 px high, caption 500, --surface-2 background, --muted text.
- kbd: mono 11, --surface-2 background, 1 px --border, radius 4, padding 1 6.
- Tooltip: popover style, caption, 6 px by 10 px padding, 150 ms delay, never contains essential information.
- Divider: 1 px --border-soft.
- Scrollbars: 10 px, transparent track, thumb --border-strong with radius 9999, hover --muted; scrollbar-gutter stable on scroll panes.
- Text selection: accent at 30 percent.
- Focus ring: 2 px --ring, offset 2, on :focus-visible for every interactive element; never remove outlines without a replacement.
- Skeleton: --surface-2 bars with radius 6 and a shimmer, same heights as the content they stand in for.

## 8. Components (implemented in later tasks; defined here so everything stays harmonious)

### 8.1 Sidebar (15.D2)
248 px, --sidebar background, 1 px --border-soft on the right. Brand row 56 px: logo 20 px plus "TreeFleet" title (sans 600 16). Search: 32 px input with magnifier icon and a kbd "Ctrl K" chip on the right, opens the palette. Section labels in micro style, 16 px above the items. Project item: 36 px row, radius 8, active = --surface-2 with a 2 px accent bar on the left. Run history rows are 52 px: status dot (6 px), short id in code-sm, relative time right-aligned in caption, and a second line with the goal text truncated to one line (body-sm --muted); active row = --surface-2. Footer pinned: Settings row 36 px with a gear icon, plus the version in caption --muted-2.

### 8.2 Topbar (15.D2)
56 px, 1 px --border-soft bottom. Left: project title (serif title-lg), then the "New task" secondary button. Center-left: the segmented control. Right: elapsed timer (code-sm, tabular), bell icon button with a count dot, "Needs you N" status badge in violet (only visible when N is above 0), settings icon button. Gaps 12 px.

### 8.3 Composer (15.D2)
A --surface card, radius 12, 1 px --border, padding 12. Textarea without its own border, serif body-lg 16 px placeholder "Describe what to build" at --muted-2, autosize 1 to 8 rows. Bottom row 40 px: routing select (ghost style, with a sparkle icon, label "Smart routing"), a hint "Ctrl Enter to run" in caption on the right, and the primary Run button with an arrow icon. Focus-within raises the card border to --accent at 60 percent. Disabled while a plan gate is open, with an explanation line. In orchestrator mode the follow-up composer is the same component, compact.

### 8.4 Run strip (15.D2)
A 40 px row with no box: left "Running" status text with a spinner, "1 of 4 tasks" and the elapsed time in caption. Right: the stages (guard, plan, approve, fleet, review, integrate) as a stepper: each step is a 24 px high chip; done = muted with a check icon, active = accent tint with accent text and a spinner, upcoming = --muted-2; thin 12 px connector lines between steps. Hover shows a tooltip with the stage duration.

### 8.5 Kanban (15.D3)
Lanes are borderless columns on --bg: header 40 px sticky (6 px status dot, label title-sm, count pill), 12 px gap, then cards. Empty lane shows a dashed 1 px --border-soft box with caption "No tasks" (and an icon). Cards: --surface, radius 12, 1 px --border, padding 12, 8 px internal gaps. Row 1: model chip left, elapsed timer right (code-sm tabular, --muted). Row 2: task title (title-sm, two lines max with ellipsis). Row 3: branch with a git-branch icon (code-sm --muted, ellipsis in the middle). Row 4 (optional): current action with a spinner or icon (body-sm, one line). Row 5 (optional): verdict badge plus the reviewer note (body-sm --muted, two lines max, "Show more" opens the drawer). Footer (optional): +N and -M counts in code-sm success/error, and a secondary button such as "Copy merge command". Hover: border strong. Selected: accent border and tint. Needs-you cards add a 2 px violet bar on the left edge. Keyboard: arrow keys move between cards, Enter opens the drawer.

### 8.6 Git graph (15.D3)
A section below the lanes with a micro title "Git graph" and a 1 px divider. Rows 28 px, node 8 px circles on a 1 px vertical line (--border-strong), branch labels in code-sm, base row muted, integrated row with a check icon. Row hover highlights the card in the lanes and vice versa. Collapsible with a chevron, remembered in localStorage.

### 8.7 Drawer (15.D4)
Container --surface, 1 px --border-soft on the left, resizable (see layout). Header 64 px: task title (serif title-lg, one line), under it the branch (code-sm --muted) with a copy icon button and the status badge; a Close icon button on the right. Tabs row (36 px) directly below with a 1 px --border-soft under it: Live output, Diff, Proof, Checks, Cost. Content area scrolls independently; padding 16 px; each tab keeps its own scroll position. Open and close with the 240 ms slide.

### 8.8 Live output (15.D4)
Toolbar 36 px: a Follow toggle (icon plus label, on by default; turns off when the user scrolls up, with a "Jump to latest" pill appearing), Copy and Pause as icon buttons with tooltips. Lines in code (mono 12.5), left gutter of timestamps in --muted-2 (hideable), tool-call lines prefixed with a small icon (pencil, terminal, search) and the target in --text; errors get a 2 px error bar on the left and error-tinted text. 80-line cap with "Show earlier lines" as a ghost button. Wrap off by default with horizontal scroll, a Wrap toggle in the toolbar.

### 8.9 Diff view (15.D4)  -- the biggest visual fix
- Summary bar (sticky, 40 px): "3 files changed", +42 in success, -7 in error (tabular), a 5-block proportion bar, then a segmented control "Unified | Split", an icon button "Collapse all / Expand all", a Copy patch icon button, and an Expand button that opens the diff in a full-width overlay (this is where Split is enabled; Split is disabled with a tooltip when the container is narrower than 900 px).
- File list: when 4 or more files, a collapsible "Files" tree above the diff with status icons; clicking scrolls to the file. Under 4 files, no tree.
- File blocks: each file is a --surface card, radius 12, 1 px --border, overflow hidden, 12 px gap between files. Sticky file header 36 px: chevron to collapse, status icon (added, modified, deleted, renamed) plus a status label, the path with the directory in --muted and the file name in --text 500 (code-sm), a copy-path icon button on hover, and +N -M on the right. Renames show "old -> new". Binary files show a "Binary file changed" row with size if known. Very large files collapse by default with "Show diff (N lines)".
- Lines: row height 20 px, code font, three columns: old line number (44 px), new line number (44 px) both right-aligned tabular --muted-2, then a 16 px marker column (+, -, space), then the code. Added and removed rows use the diff tints from section 4 and a 2 px colored bar at the left edge; context rows have no tint. Word-level highlights inside changed lines, computed by pairing consecutive removed and added lines and diffing tokens (words and punctuation); skip pairing when the lines differ by more than 60 percent to avoid noise.
- Hunk header rows: 28 px, --surface-2 background, code-sm --muted, text "@@ -a,b +c,d @@" followed by the function context when git provides it. Three context lines per hunk. A ghost "Show N hidden lines" row between hunks when the patch has gaps (expansion fetches more only if the API supports it; otherwise omit the row).
- Split view: two equal columns each with its own line numbers; removed rows on the left, added on the right, aligned by hunk pairing; context duplicated on both sides.
- Truncation: never silently. If the API says truncated, show a warning banner at the top ("Diff truncated at 8 KB of N") with a "Load full diff" button, and per-file "Show more" where possible. Empty state: serif title "No changes yet", caption "This task has not edited any files." No file tree and no summary when empty.
- Keyboard: n and p jump to next and previous file, Enter on a file header toggles it, c copies the path. No horizontal page scroll; each file block scrolls horizontally on its own with the gutters frozen (position sticky on the number columns).
- Syntax highlighting is a stretch goal and is NOT required; the DOM must leave room (one span per token class) without adding a dependency.

### 8.10 Proof, Checks, Cost (15.D4)
Proof: a vertical list of check rows 40 px (status icon, label body-sm, value in code-sm right-aligned, expandable details in code); failures expand by default. Checks: the same row pattern, grouped by source with micro labels. Cost: a table with micro-style headers, 32 px rows, right-aligned tabular numbers, a bold totals row separated by a hairline, and a small horizontal bar per row showing its share of cost; money always 4 decimals under $1 and 2 above, tokens with thousands separators.

### 8.11 Orchestrator transcript (15.D5)
Centered column 720 px wide on --bg. The goal is a user message: a --surface block radius 12 with serif body-lg text and a small "You" micro label. System and assistant messages have no bubble: serif body-lg text, a 20 px gutter icon, 24 px between messages. Worker progress is a compact card list (same pattern as kanban rows, 36 px). Tool activity rows: 32 px, icon, verb in body-sm 500, target in code-sm, duration in caption right-aligned, a chevron that expands the input and output in a code block (only if the events carry them; otherwise no chevron). A view-mode control "Verbose | Normal | Summary" in the transcript header: Verbose shows every tool row expanded to one line, Normal groups consecutive tool rows into "Edited 3 files, ran 2 commands" with a chevron, Summary shows only messages, plan, verdicts and the integration summary. Plan approval is a card (radius 12, accent border at 40 percent) with the plan steps as a numbered list, the Approve (primary) and Reject (danger ghost) buttons, and the reason text field optional. Verdict rows: task title, verdict badge, note. Integration summary: a card with a definition list (branch, checks, cost, speedup) and the Copy merge command secondary button. "Jump to latest" pill at the bottom right when scrolled up. Live work shows a typing-style "Working on <task>" row with a spinner (never fake token streaming).

### 8.12 Command palette, settings, notifications, toasts, states (15.D6)
- Palette: modal radius 16, 560 px wide, --shadow-lg over --overlay. Input row 48 px with a magnifier icon; results grouped with micro labels; rows 36 px with icon, label, optional kbd hint; active row --surface-2; no results state in serif.
- Settings: opens as a right-hand panel like the drawer or a centered modal (choose one and justify). Sections with serif title, rows with label, caption description and the control on the right, 56 px minimum height, hairline dividers, a danger zone section at the bottom.
- Notifications: bell popover 360 px, radius 12, rows 56 px with status icon, title, caption time; "Needs you" items first with the violet bar; empty state "You're all caught up".
- Toasts: bottom-right stack, 360 px max, radius 12, --surface-2 with hairline and --shadow-md, icon plus text plus optional action, auto dismiss after 5 s paused on hover, role="status" for info and role="alert" for errors, never more than 3 visible.
- Empty states: serif display or title-lg headline, one body-sm --muted sentence, at most one primary action, centered in the pane with 48 px spacing.
- Loading: skeletons with the final layout; spinners only inside buttons and for in-flight work.
- Errors: an inline banner (error tint 10 percent, 1 px error-at-30-percent border, icon, message, Retry ghost button); never a blank pane.

## 9. Responsive
1440 (design target), 1024 (sidebar collapses to 64 px icons, drawer overlays), 640 and below (sidebar becomes a top sheet, lanes stack into a single scrolling column with a lane selector, the drawer is a bottom sheet, the composer pins to the bottom). Test at 1440, 1024 and 390 wide.

## 10. Accessibility
WCAG AA text 4.5 and non-text 3.0 for every token pair actually used (the contrast test covers them). Every interactive element reachable and operable by keyboard with a visible focus ring. aria-live regions for run status and toasts. Tabs, segmented control, menus and the palette follow ARIA patterns with arrow-key navigation. Targets 32 px on desktop and 44 px on coarse pointers. prefers-reduced-motion honored. All untrusted text (diffs, logs, branch and comment text) is inserted as text nodes, never as HTML.

## 11. Definition of done for a component
Matches this spec at 1440 and 390; keyboard and screen reader checked; all states drawn (default, hover, focus, active, disabled, loading, empty, error where they apply); only tokens, no literals; existing tests green; screenshot attached to the task report.
