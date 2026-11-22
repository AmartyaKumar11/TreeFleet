/** Bell popover: derive entries from board + transcript events (no new endpoint). */

import { setSafeText } from "./safe-text.js";
import { paintEmpty } from "./pane-state.js";

const READ_KEY = "treefleet_notif_read";
const READ_CAP = 200;

function readIds() {
  try {
    const raw = JSON.parse(localStorage.getItem(READ_KEY) || "[]");
    return Array.isArray(raw) ? raw.map(String) : [];
  } catch {
    return [];
  }
}

function writeIds(ids) {
  localStorage.setItem(READ_KEY, JSON.stringify(ids.slice(0, READ_CAP)));
}

function markRead(id) {
  const ids = readIds();
  if (!ids.includes(id)) {
    ids.unshift(id);
    writeIds(ids);
  }
}

function markAllRead(entries) {
  const ids = readIds();
  entries.forEach((e) => {
    if (!ids.includes(e.id)) ids.unshift(e.id);
  });
  writeIds(ids);
}

/**
 * Derive notification entries from board snapshot.
 * @returns {{id, kind, title, detail, ts, taskId, needsYou}[]}
 */
export function deriveNotifications(board = {}) {
  const run = board.run_id || "norun";
  const out = [];
  const seen = new Set();

  function push(entry) {
    if (!entry.id || seen.has(entry.id)) return;
    seen.add(entry.id);
    out.push(entry);
  }

  if (board.awaiting) {
    push({
      id: `${run}:plan_awaiting`,
      kind: "plan_awaiting",
      title: "Plan awaiting approval",
      detail: board.goal || "Review the plan to continue",
      ts: board.started_at || null,
      taskId: null,
      needsYou: true,
    });
  }

  (board.tasks || []).forEach((task) => {
    const lane = task.lane || task.status || "";
    if (lane === "needs" || task.needs_human) {
      push({
        id: `${run}:needs:${task.id}`,
        kind: "needs_you",
        title: task.title || task.id || "Task needs you",
        detail: task.reason || "Needs your attention",
        ts: null,
        taskId: task.id,
        needsYou: true,
      });
    }
    if (lane === "ready" || task.verdict === "approve") {
      push({
        id: `${run}:ready:${task.id}`,
        kind: "ready",
        title: task.title || task.id || "Ready for review",
        detail: "Ready to merge",
        ts: null,
        taskId: task.id,
        needsYou: false,
      });
    }
    if (lane === "review") {
      push({
        id: `${run}:review:${task.id}`,
        kind: "ready",
        title: task.title || task.id || "In review",
        detail: "Awaiting review",
        ts: null,
        taskId: task.id,
        needsYou: false,
      });
    }
    const st = String(task.status || "").toLowerCase();
    if (st === "failed" || st === "timeout" || st === "timed_out" || task.error) {
      push({
        id: `${run}:fail:${task.id}`,
        kind: "failed",
        title: task.title || task.id || "Task failed",
        detail: task.error || task.reason || "Failed or timed out",
        ts: null,
        taskId: task.id,
        needsYou: true,
      });
    }
  });

  (board.events || []).forEach((event, index) => {
    const offset = event.offset != null ? event.offset : index;
    if (event.type === "awaiting_approval") {
      push({
        id: `${run}:evt:awaiting:${offset}`,
        kind: "plan_awaiting",
        title: "Plan awaiting approval",
        detail: board.goal || "Approval needed",
        ts: event.ts || null,
        taskId: null,
        needsYou: true,
      });
    } else if (event.type === "worker.exited") {
      const payload = event.payload || {};
      const status = String(payload.status || "").toLowerCase();
      if (payload.error || status === "failed" || status === "timeout" || status === "timed_out") {
        push({
          id: `${run}:evt:exit:${offset}`,
          kind: "failed",
          title: `Worker ${event.task_id || ""} ${status || "failed"}`.trim(),
          detail: payload.error || "Worker exited with failure",
          ts: event.ts || null,
          taskId: event.task_id || null,
          needsYou: true,
        });
      }
    } else if (event.type === "pr.poll_error") {
      const payload = event.payload || {};
      push({
        id: `${run}:evt:poll:${offset}`,
        kind: "failed",
        title: "PR poll failed",
        detail: payload.message || "Poll error",
        ts: event.ts || null,
        taskId: event.task_id || null,
        needsYou: false,
      });
    }
  });

  // Needs-you first
  out.sort((a, b) => Number(b.needsYou) - Number(a.needsYou));
  return out;
}

export function createNotifications({ getBoard, onOpenTask, onOpenRun }) {
  let open = false;
  let prevFocus = null;

  function root() {
    return document.getElementById("notif-popover");
  }

  function bell() {
    return document.getElementById("bell");
  }

  function badge() {
    return document.getElementById("attention-badge");
  }

  function entries() {
    return deriveNotifications(getBoard() || {});
  }

  function unread(list) {
    const read = new Set(readIds());
    return list.filter((e) => !read.has(e.id));
  }

  function syncBadge() {
    const list = entries();
    const n = unread(list).length;
    const b = badge();
    const btn = bell();
    if (b) {
      b.hidden = n === 0;
      b.textContent = String(n);
    }
    if (btn) {
      btn.setAttribute("aria-label", n ? `Notifications, ${n} unread` : "Notifications");
    }
  }

  function render() {
    const pop = root();
    if (!pop) return;
    const listEl = pop.querySelector(".notif-list");
    if (!listEl) return;
    while (listEl.firstChild) listEl.removeChild(listEl.firstChild);
    const list = entries();
    const read = new Set(readIds());
    if (!list.length) {
      paintEmpty(listEl, { body: "You're all caught up" });
      syncBadge();
      return;
    }
    list.forEach((entry) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "notif-row" + (entry.needsYou ? " is-needs" : "") + (read.has(entry.id) ? " is-read" : "");
      row.dataset.notifId = entry.id;
      const title = document.createElement("div");
      title.className = "notif-title";
      setSafeText(title, entry.title);
      const detail = document.createElement("div");
      detail.className = "notif-detail type-caption";
      setSafeText(detail, entry.detail || "");
      row.appendChild(title);
      row.appendChild(detail);
      row.addEventListener("click", () => {
        markRead(entry.id);
        close();
        if (entry.taskId && onOpenTask) {
          const task = ((getBoard() || {}).tasks || []).find((t) => t.id === entry.taskId);
          if (task) onOpenTask(task);
          else if (onOpenRun) onOpenRun();
        } else if (onOpenRun) onOpenRun();
        syncBadge();
      });
      listEl.appendChild(row);
    });
    syncBadge();
  }

  function openPop() {
    const pop = root();
    const btn = bell();
    if (!pop || !btn) return;
    prevFocus = document.activeElement;
    open = true;
    pop.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    render();
    const first = pop.querySelector(".notif-row, .notif-mark-all, .pane-empty");
    if (first && first.focus) first.focus();
  }

  function close() {
    const pop = root();
    const btn = bell();
    if (!pop) return;
    open = false;
    pop.hidden = true;
    if (btn) btn.setAttribute("aria-expanded", "false");
    if (prevFocus && prevFocus.focus) prevFocus.focus();
    prevFocus = null;
  }

  function toggle() {
    if (open) close();
    else openPop();
  }

  function bind() {
    const btn = bell();
    const pop = root();
    if (btn) {
      btn.setAttribute("aria-haspopup", "dialog");
      btn.setAttribute("aria-controls", "notif-popover");
      btn.setAttribute("aria-expanded", "false");
      btn.addEventListener("click", (event) => {
        event.stopPropagation();
        toggle();
      });
    }
    const mark = pop && pop.querySelector(".notif-mark-all");
    if (mark) {
      mark.addEventListener("click", () => {
        markAllRead(entries());
        render();
      });
    }
    document.addEventListener("keydown", (event) => {
      if (!open) return;
      if (event.key === "Escape") {
        event.preventDefault();
        close();
      }
    });
    document.addEventListener("click", (event) => {
      if (!open) return;
      const wrap = document.querySelector(".notif-wrap");
      if (wrap && !wrap.contains(event.target)) close();
    });
  }

  return { bind, open: openPop, close, render, syncBadge, isOpen: () => open, derive: deriveNotifications };
}
