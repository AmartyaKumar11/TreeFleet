/** Ctrl/Cmd+K command palette: groups, fuzzy rank, recent, a11y combobox. */

import { setSafeText } from "./safe-text.js";
import { paintEmpty } from "./pane-state.js";

const RECENT_KEY = "treefleet_palette_recent";
const RECENT_MAX = 8;

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function readRecent() {
  try {
    const raw = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
    return Array.isArray(raw) ? raw.map(String) : [];
  } catch {
    return [];
  }
}

function pushRecent(id) {
  const ids = readRecent().filter((x) => x !== id);
  ids.unshift(id);
  localStorage.setItem(RECENT_KEY, JSON.stringify(ids.slice(0, RECENT_MAX)));
}

/** Ranked fuzzy: contiguous bonus, earlier match bonus. Lower score = better. */
function fuzzyRank(query, text) {
  const q = String(query || "").toLowerCase().trim();
  const t = String(text || "").toLowerCase();
  if (!q) return 0;
  let qi = 0;
  let score = 0;
  let streak = 0;
  let first = -1;
  for (let i = 0; i < t.length && qi < q.length; i++) {
    if (t[i] === q[qi]) {
      if (first < 0) first = i;
      streak += 1;
      score += i + (streak > 1 ? -2 : 0);
      qi += 1;
    } else {
      streak = 0;
    }
  }
  if (qi < q.length) return null;
  return score + first * 0.01;
}

export function createPalette({
  getRuns,
  getTasks,
  onOpenRun,
  onJumpTask,
  onApprovePlan,
  onSetTranscriptMode,
  onOpenSettings,
  onOpenNotifications,
  onJumpLane,
  onNewRun,
  onSwitchView,
  onToggleAuto,
  onCopyMerge,
}) {
  let open = false;
  let active = 0;
  let items = [];
  let prevFocus = null;

  function root() {
    return document.getElementById("command-palette");
  }

  function inputEl() {
    return document.getElementById("command-input");
  }

  function listEl() {
    return document.getElementById("command-list");
  }

  function liveEl() {
    return document.getElementById("palette-live");
  }

  function allCommands() {
    const rows = [];

    (getRuns?.() || []).forEach((run) => {
      rows.push({
        id: `run:${run.id}`,
        group: "Runs",
        label: run.goal || run.id,
        sub: run.id,
        run: () => onOpenRun?.(run),
      });
    });

    (getTasks?.() || []).forEach((task) => {
      rows.push({
        id: `task:${task.id}`,
        group: "Tasks",
        label: task.title || task.id,
        sub: task.branch || task.id,
        run: () => onJumpTask?.(task),
      });
    });

    const actions = [
      {
        id: "action:approve",
        group: "Actions",
        label: "Approve pending plan",
        sub: "POST /api/runs/{id}/approve",
        run: () => onApprovePlan?.(),
      },
      {
        id: "action:mode-summary",
        group: "Actions",
        label: "Transcript mode: Summary",
        sub: "localStorage treefleet_transcript_mode",
        run: () => onSetTranscriptMode?.("summary"),
      },
      {
        id: "action:mode-normal",
        group: "Actions",
        label: "Transcript mode: Normal",
        sub: "localStorage treefleet_transcript_mode",
        run: () => onSetTranscriptMode?.("normal"),
      },
      {
        id: "action:mode-verbose",
        group: "Actions",
        label: "Transcript mode: Verbose",
        sub: "localStorage treefleet_transcript_mode",
        run: () => onSetTranscriptMode?.("verbose"),
      },
      {
        id: "action:settings",
        group: "Actions",
        label: "Open settings",
        sub: "client panel",
        run: () => onOpenSettings?.(),
      },
      {
        id: "action:notifications",
        group: "Actions",
        label: "Open notifications",
        sub: "client popover",
        run: () => onOpenNotifications?.(),
      },
      {
        id: "action:new-run",
        group: "Actions",
        label: "New run",
        sub: "focus composer",
        run: () => onNewRun?.(),
      },
      {
        id: "action:toggle-auto",
        group: "Actions",
        label: "Toggle auto-run",
        sub: "client preference",
        run: () => onToggleAuto?.(),
      },
      {
        id: "action:copy-merge",
        group: "Actions",
        label: "Copy merge command",
        sub: "clipboard",
        run: () => onCopyMerge?.(),
      },
    ];
    rows.push(...actions);

    const nav = [
      {
        id: "nav:kanban",
        group: "Navigation",
        label: "Kanban view",
        sub: null,
        run: () => onSwitchView?.("kanban"),
      },
      {
        id: "nav:orch",
        group: "Navigation",
        label: "Orchestrator view",
        sub: null,
        run: () => onSwitchView?.("orchestrator"),
      },
      {
        id: "nav:lane-working",
        group: "Navigation",
        label: "Jump to Working lane",
        sub: null,
        run: () => onJumpLane?.("working"),
      },
      {
        id: "nav:lane-needs",
        group: "Navigation",
        label: "Jump to Needs you lane",
        sub: null,
        run: () => onJumpLane?.("needs"),
      },
      {
        id: "nav:lane-review",
        group: "Navigation",
        label: "Jump to In review lane",
        sub: null,
        run: () => onJumpLane?.("review"),
      },
      {
        id: "nav:lane-ready",
        group: "Navigation",
        label: "Jump to Ready lane",
        sub: null,
        run: () => onJumpLane?.("ready"),
      },
    ];
    rows.push(...nav);
    return rows;
  }

  function filtered(query) {
    const all = allCommands();
    const q = String(query || "").trim();
    if (!q) {
      const recent = readRecent();
      const byId = new Map(all.map((r) => [r.id, r]));
      const rows = recent.map((id) => byId.get(id)).filter(Boolean);
      return rows.length ? rows : all.slice(0, 12);
    }
    const scored = [];
    for (const row of all) {
      const hay = `${row.label} ${row.sub || ""} ${row.group}`;
      const rank = fuzzyRank(q, hay);
      if (rank == null) continue;
      scored.push({ row, rank });
    }
    scored.sort((a, b) => a.rank - b.rank);
    return scored.map((s) => s.row);
  }

  function announce(count) {
    const live = liveEl();
    if (live) live.textContent = count ? `${count} results` : "No results";
  }

  function renderList(query) {
    const list = listEl();
    if (!list) return;
    clear(list);
    items = filtered(query);
    active = Math.min(active, Math.max(0, items.length - 1));
    announce(items.length);
    if (!items.length) {
      paintEmpty(list, { body: "No matches" });
      const empty = list.querySelector(".pane-empty, .transcript-empty");
      if (empty) empty.classList.add("palette-empty");
      return;
    }

    let lastGroup = "";
    items.forEach((item, index) => {
      if (item.group !== lastGroup) {
        lastGroup = item.group;
        const g = document.createElement("div");
        g.className = "palette-group type-micro";
        g.textContent = item.group;
        list.appendChild(g);
      }
      const option = document.createElement("div");
      option.className = "palette-item" + (index === active ? " is-active" : "");
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", String(index === active));
      option.id = `palette-opt-${index}`;
      option.tabIndex = -1;
      const label = document.createElement("div");
      label.className = "palette-label";
      setSafeText(label, item.label);
      option.appendChild(label);
      if (item.sub) {
        const sub = document.createElement("div");
        sub.className = "palette-sub type-caption";
        setSafeText(sub, item.sub);
        option.appendChild(sub);
      }
      option.addEventListener("click", () => activate(item));
      option.addEventListener("mousemove", () => {
        if (active !== index) {
          active = index;
          renderList(inputEl()?.value || "");
        }
      });
      list.appendChild(option);
    });
    const input = inputEl();
    if (input) input.setAttribute("aria-activedescendant", items.length ? `palette-opt-${active}` : "");
  }

  function activate(item) {
    if (!item) return;
    pushRecent(item.id);
    close();
    item.run?.();
  }

  function trapFocus(event) {
    if (!open || event.key !== "Tab") return;
    const el = root();
    if (!el) return;
    const focusables = [...el.querySelectorAll("input, button, [tabindex]:not([tabindex='-1'])")].filter(
      (n) => !n.disabled && n.offsetParent !== null,
    );
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function openPalette() {
    const el = root();
    if (!el) return;
    if (!prevFocus) prevFocus = document.activeElement;
    open = true;
    el.hidden = false;
    active = 0;
    const input = inputEl();
    if (input) {
      input.value = "";
      input.setAttribute("role", "combobox");
      input.setAttribute("aria-autocomplete", "list");
      input.setAttribute("aria-expanded", "true");
      input.setAttribute("aria-controls", "command-list");
      input.focus();
    }
    renderList("");
  }

  function close() {
    const el = root();
    if (!el) return;
    open = false;
    el.hidden = true;
    const input = inputEl();
    if (input) input.setAttribute("aria-expanded", "false");
    if (prevFocus && prevFocus.focus) {
      try {
        prevFocus.focus();
      } catch {
        /* ignore */
      }
    }
    prevFocus = null;
  }

  function isEditableTarget(target) {
    if (!target || !target.tagName) return false;
    const tag = target.tagName.toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return true;
    if (target.isContentEditable) return true;
    return false;
  }

  function bind() {
    document.addEventListener("keydown", (event) => {
      const mod = event.metaKey || event.ctrlKey;
      if (mod && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (open) close();
        else openPalette();
        return;
      }
      if (!open && event.key === "/" && !isEditableTarget(event.target)) {
        event.preventDefault();
        openPalette();
        return;
      }
      if (!open) return;
      trapFocus(event);
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        active = Math.min(items.length - 1, active + 1);
        renderList(inputEl()?.value || "");
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        active = Math.max(0, active - 1);
        renderList(inputEl()?.value || "");
      } else if (event.key === "Home") {
        event.preventDefault();
        active = 0;
        renderList(inputEl()?.value || "");
      } else if (event.key === "End") {
        event.preventDefault();
        active = Math.max(0, items.length - 1);
        renderList(inputEl()?.value || "");
      } else if (event.key === "Enter") {
        event.preventDefault();
        activate(items[active]);
      }
    });
    const input = inputEl();
    if (input) {
      input.addEventListener("input", () => {
        active = 0;
        renderList(input.value);
      });
    }
    const backdrop = document.getElementById("command-backdrop");
    if (backdrop) backdrop.addEventListener("click", close);
    const search = document.getElementById("sidebar-search");
    if (search) {
      search.addEventListener("click", () => {
        prevFocus = search;
        openPalette();
      });
    }
  }

  return { bind, open: openPalette, close, isOpen: () => open };
}
