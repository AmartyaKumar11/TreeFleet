import { createOrchestrator } from "./orchestrator.js";
import { costRole, createDrawer } from "./drawer.js";
import { createGitGraph } from "./git-graph.js";
import { createPalette } from "./palette.js";
import { statusClass, statusKey, statusLabel, statusToken } from "./status.js";
import { setSafeText, sanitizeBidi, escText, escAttr } from "./safe-text.js";

window.__tfStatusToken = statusToken;
window.__tfStatusKey = statusKey;

const token = document.querySelector('meta[name="tf-token"]').content;
const replay = document.documentElement.dataset.replay === "true";
const STAGES = ["guard", "plan", "approve", "fleet", "review", "integrate"];
let started = 0;
let finished = 0;
let selected = null;
let pinned = false;
let state = {};
let streamFor = "";
let liveOffsets = new Set();
let liveFingerprints = new Set();
let lastNeedsHuman = false;
let baseTitle = "TreeFleet";
let view = "kanban";
let mobileLane = "working";
const flipPrev = new Map();
let orch = null;
let drawer = null;
let graph = null;
let palette = null;
let optimistic = null;
let laneSig = "";
let lastCounts = { working: 0, needs: 0, review: 0, ready: 0 };
let boardReady = false;
const laneRailExpanded = new Set();
const mobileLaneByRun = new Map();
let cardFocusId = null;

/** @deprecated Use escText / escAttr. Kept as escText alias for call-site migration. */
function esc(value) {
  return escText(value);
}

function eventFingerprint(event) {
  const payload = (event && event.payload) || {};
  return [
    event && event.type,
    event && event.task_id,
    event && event.seq,
    event && event.ts,
    payload.line,
    payload.tool,
    payload.path,
    payload.ok,
  ].join("|");
}

function seedLiveFromBoard(events) {
  liveOffsets = new Set();
  liveFingerprints = new Set();
  for (const event of events || []) {
    if (!event) continue;
    if (event.offset != null) liveOffsets.add(Number(event.offset));
    liveFingerprints.add(eventFingerprint(event));
  }
}

function auth(extra) {
  const headers = { Authorization: `Bearer ${token}` };
  if (extra) Object.assign(headers, extra);
  return headers;
}

function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function relativeTime(ts) {
  const stamp = Number(ts);
  if (!Number.isFinite(stamp) || stamp <= 0) return "";
  const delta = Math.max(0, Math.floor(Date.now() / 1000 - stamp));
  if (delta < 45) return "just now";
  if (delta < 3600) return `${Math.max(1, Math.floor(delta / 60))} min ago`;
  if (delta < 86400) return `${Math.max(1, Math.floor(delta / 3600))} h ago`;
  if (delta < 172800) return "yesterday";
  try {
    return new Date(stamp * 1000).toLocaleDateString();
  } catch (_err) {
    return "";
  }
}

function autosizeComposer(textarea) {
  if (!textarea) return;
  const compact = !!textarea.closest(".composer.is-compact");
  const maxRows = compact ? 6 : 8;
  const styles = window.getComputedStyle(textarea);
  const line = Number.parseFloat(styles.lineHeight) || (compact ? 36 : 26);
  const minH = compact ? 36 : line;
  const max = line * maxRows;
  textarea.style.height = "auto";
  const next = Math.min(Math.max(textarea.scrollHeight, minH), max);
  textarea.style.height = `${next}px`;
  textarea.style.overflowY = textarea.scrollHeight > max + 1 ? "auto" : "hidden";
}

function syncComposerEnabled(data) {
  const gateOpen = !!(data && data.awaiting);
  const goal = document.getElementById("goal");
  const run = document.getElementById("run");
  const gate = document.getElementById("composer-gate");
  const composer = document.getElementById("new-run");
  const empty = !String((goal && goal.value) || "").trim();
  if (composer) composer.classList.toggle("is-disabled", gateOpen);
  if (gate) gate.hidden = !gateOpen;
  if (goal) goal.disabled = gateOpen || replay;
  if (run) {
    run.disabled = gateOpen || replay || empty || run.classList.contains("is-loading");
  }
  const followGoal = document.getElementById("follow-goal");
  const followRun = document.getElementById("follow-run");
  if (followGoal) followGoal.disabled = gateOpen || replay;
  if (followRun) {
    const followEmpty = !String((followGoal && followGoal.value) || "").trim();
    followRun.disabled = gateOpen || replay || followEmpty;
  }
}

function setRunLoading(loading) {
  const run = document.getElementById("run");
  if (!run) return;
  run.classList.toggle("is-loading", loading);
  const icon = run.querySelector(".icon use");
  if (icon) {
    icon.setAttribute("href", loading ? "/static/icons.svg#icon-loader-circle" : "/static/icons.svg#icon-arrow-right");
  }
  if (loading) run.disabled = true;
  else syncComposerEnabled(state);
}

function lane(task) {
  return task.lane || "working";
}

function humanizeStatus(raw) {
  const key = String(raw || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "_");
  const map = {
    timed_out: "Run timed out",
    failed: "Run failed",
    cancelled: "Run cancelled",
    canceled: "Run cancelled",
    blocked: "Run blocked",
    needs_review: "Needs review",
    needs_you: "Needs you",
    running: "Running",
    done: "Done",
    success: "Done",
    stopped: "Stopped",
    error: "Run failed",
  };
  if (map[key]) return map[key];
  // Stage-like tokens: "Failed at fleet" already human; leave prose alone.
  if (/^[a-z0-9_]+$/i.test(String(raw || "").trim()) && map[String(raw || "").trim().toLowerCase()]) {
    return map[String(raw || "").trim().toLowerCase()];
  }
  return String(raw || "");
}

function isTerminalStatus(task) {
  const s = String(task.status || "").toLowerCase();
  return (
    s === "done" ||
    s === "failed" ||
    s === "timed_out" ||
    s === "cancelled" ||
    s === "canceled" ||
    s === "stopped" ||
    s === "error" ||
    s === "blocked"
  );
}

function isRunning(task) {
  return lane(task) === "working" && !!task.action && !isTerminalStatus(task);
}

function needsCount(counts) {
  return counts.needs || 0;
}

function notifyHuman(count) {
  const badge = document.getElementById("attention-badge");
  const legacy = document.getElementById("attention");
  const needs = count > 0;
  if (needs) {
    if (badge) {
      badge.hidden = false;
      badge.textContent = String(count);
    }
    if (legacy) {
      legacy.hidden = false;
      legacy.textContent = `Needs you ${count}`;
    }
    document.title = sanitizeBidi(`(${count}) ${baseTitle}`);
    if (!lastNeedsHuman && typeof Notification !== "undefined") {
      try {
        if (Notification.permission === "granted") {
          new Notification("TreeFleet needs you", { body: "A plan or task is waiting on the board." });
        } else if (Notification.permission === "default") {
          Notification.requestPermission();
        }
      } catch (_err) {
        /* ignore */
      }
    }
  } else {
    if (badge) badge.hidden = true;
    if (legacy) legacy.hidden = true;
    document.title = sanitizeBidi(baseTitle);
  }
  lastNeedsHuman = needs;
}

function elapsedText() {
  if (!started) return "0:00";
  return clock((finished || Date.now() / 1000) - started);
}

function isMobileBoard() {
  return window.matchMedia("(max-width: 1023px)").matches;
}

const DRAWER_WIDTH_KEY = "treefleet.drawer.width";

function readDrawerWidth() {
  try {
    const raw = localStorage.getItem(DRAWER_WIDTH_KEY);
    const n = raw ? Number(raw) : 480;
    if (Number.isFinite(n)) return Math.min(720, Math.max(360, n));
  } catch (_err) {
    /* ignore */
  }
  return 480;
}

function writeDrawerWidth(px) {
  try {
    localStorage.setItem(DRAWER_WIDTH_KEY, String(px));
  } catch (_err) {
    /* ignore */
  }
}

function applyDrawerWidth(px) {
  const width = Math.min(720, Math.max(360, Math.round(px)));
  document.documentElement.style.setProperty("--drawer-width", `${width}px`);
  const handle = document.getElementById("drawer-resize");
  if (handle) handle.setAttribute("aria-valuenow", String(width));
  return width;
}

function syncSheet(taskOpen) {
  const drawerEl = document.getElementById("task-drawer") || document.querySelector(".drawer");
  const body = document.querySelector(".board-body");
  const backdrop = document.getElementById("drawer-backdrop");
  const close = document.getElementById("drawer-close");
  const mobile = isMobileBoard();
  if (body) body.classList.toggle("has-drawer", !!taskOpen && !mobile);
  if (drawerEl) {
    drawerEl.hidden = !taskOpen;
    drawerEl.classList.toggle("is-open", !!(taskOpen && mobile));
    drawerEl.classList.toggle("is-visible", !!taskOpen);
  }
  if (backdrop) backdrop.hidden = !(taskOpen && mobile);
  if (close) close.hidden = !taskOpen;
  // Opening the mobile sheet must close the off-canvas nav so its footer cannot cover Close.
  if (taskOpen && mobile) {
    document.querySelector(".shell")?.classList.remove("sidebar-open");
    document.getElementById("sidebar")?.classList.remove("is-sheet-open");
  }
  if (taskOpen) requestAnimationFrame(() => syncLaneRails());
  else requestAnimationFrame(() => syncLaneRails());
}

function showDrawerEmpty() {
  const returnId = selected?.id || cardFocusId;
  selected = null;
  pinned = false;
  document.querySelectorAll(".card.is-selected").forEach((el) => el.classList.remove("is-selected"));
  syncSheet(false);
  if (returnId) {
    const card = document.querySelector(`.card[data-task-id="${CSS.escape(String(returnId))}"]`);
    if (card) {
      card.tabIndex = 0;
      card.focus({ preventScroll: true });
      cardFocusId = String(returnId);
    }
  }
}

window.__tfClearSelection = showDrawerEmpty;

function filesFor(task) {
  const rows = state.worktrees || [];
  const wanted = String(task.branch || "");
  if (wanted) {
    const row = rows.find((item) => String(item.branch || "") === wanted);
    if (row) return Array.isArray(row.committed) ? row.committed : row.files || [];
  }
  return task.files || [];
}

function changedLabel(row) {
  const files = row.files || [];
  if (!files.length) return String(row.changed == null ? 0 : row.changed);
  let added = 0;
  let removed = 0;
  files.forEach((file) => {
    added += file.additions || 0;
    removed += file.deletions || 0;
  });
  return `${files.length} +${added} -${removed}`;
}

function lineCounts(files) {
  let added = 0;
  let removed = 0;
  files.forEach((file) => {
    if (typeof file === "string") return;
    added += file.additions || 0;
    removed += file.deletions || 0;
  });
  return { added, removed, count: files.length };
}

function worktreeFor(task) {
  const branch = String(task.branch || "");
  if (!branch) return "";
  const row = (state.worktrees || []).find((item) => String(item.branch || "") === branch);
  return row ? String(row.path || "") : "";
}

function formatUsd(value, label) {
  const tag = String(label || "");
  if (/usage not reported/i.test(tag)) return "usage not reported";
  if (/unpriced|rate unknown/i.test(tag)) return "unpriced";
  if (value == null || value === "" || Number.isNaN(Number(value))) return "unpriced";
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return "unpriced";
  if (n < 0.0001) return "<$0.0001";
  return `$${n.toFixed(4)}`;
}

function moneyCell(row) {
  const text = formatUsd(row.usd, row.label);
  if (text === "unpriced" || text === "usage not reported") return `<span class='pill'>${escText(text)}</span>`;
  return escText(text);
}

function captureFlip() {
  flipPrev.clear();
  document.querySelectorAll(".card[data-task-id]").forEach((el) => {
    flipPrev.set(el.dataset.taskId, el.getBoundingClientRect());
  });
}

function playFlip() {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  document.querySelectorAll(".card[data-task-id]").forEach((el) => {
    const prev = flipPrev.get(el.dataset.taskId);
    if (!prev) return;
    const next = el.getBoundingClientRect();
    const dx = prev.left - next.left;
    const dy = prev.top - next.top;
    if (!dx && !dy) return;
    el.style.transform = `translate(${dx}px, ${dy}px)`;
    el.style.transition = "transform 0s";
    requestAnimationFrame(() => {
      el.style.transition = "transform 180ms ease";
      el.style.transform = "";
    });
  });
}

function modelIcon() {
  return `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#icon-sparkles"></use></svg>`;
}

function gitGlyph() {
  return `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#icon-git-branch"></use></svg>`;
}

function middleTruncate(text, max = 36) {
  const value = String(text || "");
  if (value.length <= max) return value;
  const parts = value.split("/");
  const last = parts[parts.length - 1] || value.slice(-8);
  const prefixBudget = Math.max(6, max - last.length - 1);
  let prefix = value.slice(0, prefixBudget);
  if (parts.length > 1) {
    const head = parts.slice(0, Math.min(2, parts.length - 1)).join("/");
    prefix = head.length <= prefixBudget ? head : value.slice(0, prefixBudget);
  }
  return `${prefix}…/${last}`;
}

function taskAge(task) {
  if (task.age_s != null) return clock(task.age_s);
  if (!started) return "";
  const end = finished || Date.now() / 1000;
  return clock(end - started);
}

function setLaneCounts(tasks, awaiting) {
  const counts = { working: 0, needs: awaiting ? 1 : 0, review: 0, ready: 0, done: 0 };
  tasks.forEach((task) => {
    counts[lane(task)] = (counts[lane(task)] || 0) + 1;
  });
  Object.keys(counts).forEach((id) => {
    const el = document.querySelector(`#lane-${id} .n`);
    if (el) {
      el.textContent = boardReady ? String(counts[id]) : "";
      el.hidden = !boardReady;
    }
    const tab = document.querySelector(`.mobile-lane-tabs [data-lane="${id}"]`);
    if (tab) {
      const n = tab.querySelector(".n");
      if (n) {
        n.textContent = boardReady ? String(counts[id]) : "";
        n.hidden = !boardReady;
      } else if (boardReady) tab.textContent = `${tab.dataset.label} ${counts[id]}`;
    }
  });
  syncDoneLane(counts.done > 0);
  const doneCount = document.querySelector("#lane-done .n");
  if (doneCount) doneCount.textContent = String(counts.done);
  const doneTab = document.querySelector('.mobile-lane-tabs [data-lane="done"]');
  if (doneTab) {
    const n = doneTab.querySelector(".n");
    if (n) n.textContent = String(counts.done);
    else doneTab.textContent = `Done ${counts.done}`;
  }
  const runId = state.run_id || "";
  if (runId && !mobileLaneByRun.has(runId)) {
    const order = ["working", "needs", "review", "ready", "done"];
    const first = order.find((id) => counts[id] > 0) || "working";
    mobileLane = first;
    mobileLaneByRun.set(runId, first);
  }
  return counts;
}

function syncDoneLane(show) {
  const lanes = document.querySelector(".lanes");
  const tabs = document.querySelector(".mobile-lane-tabs");
  const lane = document.getElementById("lane-done");
  const tab = tabs ? tabs.querySelector('[data-lane="done"]') : null;
  if (!show) {
    if (lane) lane.remove();
    if (tab) tab.remove();
    if (mobileLane === "done") mobileLane = "working";
    return;
  }
  if (!lane && lanes) {
    const created = document.createElement("div");
    created.className = "lane";
    created.id = "lane-done";
    created.dataset.lane = "done";
    created.innerHTML =
      '<h2 class="lane-head"><span class="dot green" aria-hidden="true"></span><span class="lane-label type-title-sm">Done</span> <span class="n count-pill">0</span></h2><div class="stack" id="done" role="list"></div>';
    lanes.appendChild(created);
  }
  if (!tab && tabs) {
    const created = document.createElement("button");
    created.type = "button";
    created.dataset.lane = "done";
    created.dataset.label = "Done";
    created.setAttribute("aria-selected", "false");
    created.textContent = "Done 0";
    created.addEventListener("click", () => {
      mobileLane = "done";
      applyMobileLane();
    });
    tabs.appendChild(created);
  }
}

function runStripState(data, counts) {
  const stage = (optimistic && optimistic.stage) || data.stage || "guard";
  const awaiting = optimistic && optimistic.awaiting === null ? null : data.awaiting;
  const total = (data.tasks || []).length || (awaiting ? awaiting.length : 0);
  const active = counts.working || (optimistic && optimistic.starting ? total : 0);
  const msg = String(data.message || "");
  const cancelled = stage === "cancelled" || /cancell?ed/i.test(msg);
  const failed =
    stage === "timed_out" ||
    stage === "failed" ||
    stage === "error" ||
    (/fail|timed.?out/i.test(msg) && !cancelled);
  const blocked =
    !(optimistic && optimistic.stage === "fleet") &&
    ((data.message && /blocked/i.test(data.message)) ||
      (data.events || []).some(
        (event) =>
          event.type === "guard.decision" &&
          (event.payload || {}).stage === "input" &&
          (event.payload || {}).allowed === false,
      ));
  if (cancelled) return { line: "Cancelled", stage: stage === "cancelled" ? "guard" : stage, tone: "muted" };
  if (failed || blocked) {
    const failStage = blocked ? "guard" : stage === "timed_out" ? "fleet" : stage;
    return { line: `Failed at ${failStage}`, stage: failStage, tone: "error" };
  }
  if (awaiting || stage === "approve") return { line: "Awaiting approval", stage: "approve", tone: "needs" };
  if (stage === "done") return { line: "Done", stage: "integrate", tone: "success" };
  if (data.status_line && data.status_stage) {
    return { line: data.status_line, stage: data.status_stage, tone: "running" };
  }
  if (data.pr_mode && (stage === "awaiting_prs" || stage === "pr")) {
    return { line: "Running · PRs open", stage: "pr", tone: "running" };
  }
  if (stage === "integrate") return { line: `Running · ${active || 0} of ${total || 0} tasks`, stage: "integrate", tone: "running" };
  if (stage === "review") return { line: `Running · ${active || 0} of ${total || 0} tasks`, stage: "review", tone: "running" };
  if (stage === "fleet" || active || (optimistic && optimistic.starting)) {
    return {
      line: `Running · ${active || total || 0} of ${total || active || 0} tasks`,
      stage: "fleet",
      tone: "running",
    };
  }
  if (stage === "plan") return { line: "Running · Planning", stage: "plan", tone: "running" };
  if (stage === "guard") return { line: "Running · Guarding", stage: "guard", tone: "running" };
  return { line: "Idle", stage, tone: "muted" };
}

function updateRunStrip(data, counts) {
  const info = runStripState(data, counts);
  const statusEl = document.getElementById("run-status");
  if (statusEl) {
    statusEl.textContent = info.line;
    statusEl.dataset.tone = info.tone || "";
    statusEl.classList.toggle("is-error", info.tone === "error");
    statusEl.classList.toggle("is-needs", info.tone === "needs");
  }
  const shown = elapsedText();
  document.getElementById("elapsed").textContent = shown;
  const metricsElapsed = document.getElementById("metrics-elapsed");
  if (metricsElapsed) metricsElapsed.textContent = `Elapsed ${shown}`;
  const spinner = document.getElementById("run-strip-spinner");
  const needsIcon = document.getElementById("run-strip-needs");
  const alert = document.getElementById("run-strip-alert");
  const failed = info.tone === "error";
  const needs = info.tone === "needs";
  const activeWork = info.tone === "running";
  if (spinner) spinner.hidden = !activeWork;
  if (needsIcon) needsIcon.hidden = !needs;
  if (alert) alert.hidden = !failed;
  const last = document.querySelector('#stage-stepper li[data-stage="integrate"], #stage-stepper li[data-stage="pr"]');
  if (last) {
    last.dataset.stage = data.pr_mode ? "pr" : "integrate";
    const label = last.querySelector(".stage-label");
    if (label) label.textContent = data.pr_mode ? "pr" : "integrate";
  }
  const names = [...document.querySelectorAll("#stage-stepper li")].map((item) => item.dataset.stage);
  let idx = names.indexOf(info.stage);
  if (idx < 0 && failed) idx = Math.max(0, names.indexOf("fleet"));
  if (info.line === "Done" || data.stage === "done") idx = names.length;
  const stageIndex = Math.min(Math.max(idx < 0 ? 0 : idx, 0), names.length - 1);
  const compact = document.getElementById("run-status-compact");
  if (compact) {
    const label = names[stageIndex] || info.stage || "guard";
    compact.textContent = `Stage ${stageIndex + 1} of ${names.length}: ${label}`;
  }
  if (statusEl && /^(timed_out|failed|cancelled|canceled|blocked)$/i.test(String(info.line || ""))) {
    statusEl.textContent = humanizeStatus(info.line);
  }
  document.querySelectorAll("#stage-stepper li").forEach((item, i) => {
    const done = idx > i || (info.line === "Done" && !failed);
    const current = !done && i === idx;
    const isFailed = failed && current;
    item.classList.toggle("is-done", done && !isFailed);
    item.classList.toggle("is-current", current && !isFailed);
    item.classList.toggle("is-failed", isFailed);
    const icon = item.querySelector(".stage-icon use");
    if (icon) {
      if (isFailed) icon.setAttribute("href", "/static/icons.svg#icon-alert-circle");
      else if (current) icon.setAttribute("href", "/static/icons.svg#icon-loader-circle");
      else icon.setAttribute("href", "/static/icons.svg#icon-check");
    }
    const svg = item.querySelector(".stage-icon");
    if (svg) {
      svg.classList.toggle("stage-icon-spin", current && !isFailed);
      svg.style.display = current || isFailed || done ? "inline-block" : "none";
    }
    item.removeAttribute("title");
  });
  document.querySelectorAll("#stage-dots li").forEach((item, i) => {
    const done = idx > i || (info.line === "Done" && !failed);
    const current = !done && i === idx;
    item.classList.toggle("is-done", done);
    item.classList.toggle("is-current", current);
    item.classList.toggle("is-upcoming", !done && !current);
  });
}

function runStatusMeta(item, data) {
  const id = String(item.id || "idle");
  let status = String(item.status || "");
  if (!status && id === data.run_id) {
    if (data.needs_human || data.awaiting) status = "needs";
    else if (data.stage === "done") status = "done";
    else if (/fail|timed_out|blocked/i.test(String(data.message || "")) || data.stage === "timed_out") status = "error";
    else status = "running";
  }
  if (!status) status = "running";
  const fake = { status, lane: status === "needs" ? "needs" : "", needs_human: status === "needs" };
  const key = statusKey(fake);
  return { cls: statusClass(key), label: statusLabel(key), key, token: statusToken(key) };
}

function renderRunHistory(data) {
  const runList = document.getElementById("run-history");
  if (!runList) return;
  runList.innerHTML = "";
  const runs =
    Array.isArray(data.runs) && data.runs.length
      ? data.runs
      : [{ id: data.run_id || "idle", goal: data.goal || "", started: data.started_at, status: "" }];
  runs.forEach((item, index) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("role", "option");
    const id = String(item.id || "idle");
    const goal = String(item.goal != null ? item.goal : id === data.run_id ? data.goal || "" : "");
    const meta = runStatusMeta(item, data);
    if (id === data.run_id) btn.setAttribute("aria-current", "true");
    btn.dataset.runId = id;
    btn.dataset.index = String(index);
    btn.setAttribute(
      "aria-label",
      sanitizeBidi(`${meta.label} run ${id}${goal ? `: ${goal}` : ""}`),
    );
    btn.addEventListener("click", () => {
      if (id && id !== "idle") {
        selectedRun = id;
        refresh();
      }
    });
    btn.innerHTML = `<span class="dot ${meta.cls}" aria-hidden="true"></span><span class="run-id">${escText(
      id,
    )}</span><span class="run-time" data-started="${escAttr(item.started || "")}">${escText(
      relativeTime(item.started),
    )}</span><span class="run-goal">${escText(goal)}</span>`;
    li.appendChild(btn);
    runList.appendChild(li);
  });
}

function refreshRelativeTimes() {
  document.querySelectorAll(".run-list .run-time[data-started]").forEach((el) => {
    el.textContent = relativeTime(el.dataset.started);
  });
}

function argumentFailure(payload) {
  const body = payload || {};
  if (body.stage === "tool_result") return true;
  const reasons = body.reasons || body.message || "";
  const text = (Array.isArray(reasons) ? reasons.join(" ") : String(reasons)).toLowerCase();
  return /validation error|field required|error executing tool|bad_args|malformed tool result/.test(text);
}

function reviewNote(task) {
  const raw = String(task.reason || "").trim();
  const suggestions = Array.isArray(task.suggestions) ? task.suggestions.filter(Boolean) : [];
  const blob = `${raw} ${task.error || ""}`.toLowerCase();
  const needs =
    lane(task) === "needs" ||
    task.needs_human ||
    task.status === "needs_review" ||
    task.status === "failed" ||
    task.verdict === "reject" ||
    task.verdict === "revise";
  let text = "";
  let tone = "";
  if (needs) {
    tone = "needs";
    if (/no new diff|no new change|no changes|unchanged/.test(blob)) text = "No new changes since last review";
    else if (/conflict/.test(blob)) text = "Merge conflict";
    else if (/git\s+-c|[a-z]:\\/.test(raw) || /[/\\]/.test(raw)) text = "A check failed";
    else if (task.pr_number && (task.status === "needs_review" || task.pr_status === "cancelled") && raw) text = raw;
    else text = "Needs a human decision";
  } else if (task.pr_number && raw === "revision made no commit") {
    tone = "needs";
    text = raw;
  } else if (task.verdict === "approve" || lane(task) === "ready") {
    tone = "ok";
    text = suggestions.length ? "Approved with notes" : "Checks passed";
  }
  const limit = task.pr_number && (task.status === "needs_review" || task.pr_status === "cancelled") ? 120 : 60;
  return { text: text.slice(0, limit), tone };
}

function formatLineCounts(counts) {
  const addCls = counts.added ? "add" : "add is-zero";
  const delCls = counts.removed ? "del" : "del is-zero";
  return `<span class="chip lines"><span class="${addCls}">+${counts.added}</span> <span class="${delCls}">−${counts.removed}</span></span>`;
}

function cardStatusWords(task) {
  return statusLabel(task);
}

function paintCardShell(button, task) {
  const selected = button.classList.contains("is-selected");
  const hot = button.classList.contains("is-graph-hot");
  button.className = "card";
  if (lane(task) === "needs" || task.needs_human || task.status === "needs_review") {
    button.classList.add("is-needs");
  }
  if (selected) button.classList.add("is-selected");
  if (hot) button.classList.add("is-graph-hot");
  button.type = "button";
  button.dataset.taskId = task.id;
  button.setAttribute("role", "listitem");
  // Status first with an em dash so get_by_label("Isolation proof") still hits the pane only.
  button.setAttribute(
    "aria-label",
    sanitizeBidi(`${cardStatusWords(task)} — ${task.title || task.id}`),
  );
}

function fillCard(button, task) {
  paintCardShell(button, task);
  const files = filesFor(task);
  const counts = lineCounts(files);
  const running = isRunning(task);
  const verdictLabel =
    task.status === "needs_review"
      ? "needs_review"
      : lane(task) === "working" && !task.verdict
        ? ""
        : task.verdict || "";
  const chips = [];
  (task.checks || []).forEach((check) => {
    const name = typeof check === "string" ? check : check.name || "check";
    const passed = typeof check === "string" ? true : check.passed !== false;
    const label = `${name}${passed ? " pass" : " fail"}`;
    chips.push(
      `<span class="chip ${passed ? "pass" : "fail"}" title="${escAttr(label)}">${escText(label)}</span>`,
    );
  });
  if (task.pr_chip && !task.pr_number) {
    chips.push(`<span class="chip pr">${escText(task.pr_chip)}</span>`);
  }
  if (task.pr_number) {
    const merged = task.pr_status === "merged" ? " merged" : "";
    chips.push(
      `<span class="chip pr" data-pr-url="${escAttr(task.pr_url || "")}">PR #${escText(task.pr_number)}${merged}</span>`,
    );
    const failing = task.pr_checks === "failing";
    const passing = task.pr_checks === "passing";
    const label = passing ? "checks passing" : failing ? "checks failing" : "checks pending";
    chips.push(`<span class="chip ${passing ? "pass" : failing ? "fail" : ""}">${label}</span>`);
  }
  if (counts.count) chips.push(formatLineCounts(counts));
  if (task.round) chips.push(`<span class="chip round">revise ${escText(task.round)}</span>`);
  const note = reviewNote(task);
  const fullNote = note.text;
  const noteHtml = fullNote
    ? `<div class="note-row"><div class="${note.tone === "needs" ? "reason" : "note"}" title="${escAttr(
        fullNote,
      )}">${escText(fullNote)}</div><button type="button" class="btn ghost show-more" data-show-more="1">Show more</button></div>`
    : "";
  const age = taskAge(task);
  const key = statusKey(task);
  let action = "";
  if (running) {
    action = `<div class="action-line"><span class="spinner" aria-hidden="true"></span><span class="action-text">${escText(
      task.action,
    )}</span></div>`;
  } else if (key === "stopped" || key === "failed") {
    const reason = String(task.reason || task.error || task.status || "").trim();
    action = `<div class="action-line is-stopped"><span class="action-text muted">${escText(
      reason ? `Stopped · ${reason}` : "Stopped",
    )}</span></div>`;
  }
  const verdictClass = ["approve", "reject", "revise", "needs_review"].includes(verdictLabel) ? verdictLabel : "";
  const badgeClass = verdictClass || key;
  const verdict = verdictLabel
    ? `<div class="verdict-row"><span class="chip verdict badge-status status-${escAttr(
        badgeClass,
      )} ${verdictClass}">${escText(verdictLabel.replaceAll("_", " "))}</span>${noteHtml}</div>`
    : noteHtml;
  const branchFull = task.branch || task.id || "";
  const mergeCmd = task.pr_number
    ? `gh pr merge ${task.pr_number} --repo ${state.pr_repo || ""} --squash`
    : `git -C ${state.repo || ""} merge ${task.branch || task.id}`;
  const body = `
    <div class="model-row">
      <span class="model chip">${modelIcon()}<span class="model-text">${escText(task.model || "model")}</span></span>
      <span class="age timer type-code-sm"${task.age_s != null ? ' data-fixed="1"' : ""}>${escText(age)}</span>
    </div>
    <div class="title type-title-sm" title="${escAttr(task.title || "")}">${escText(task.title)}</div>
    <span class="branch type-code-sm" title="${escAttr(branchFull)}" data-branch="${escAttr(branchFull)}">${gitGlyph()}<span class="branch-text">${escText(
      middleTruncate(branchFull),
    )}</span></span>
    ${action}
    ${verdict}
    ${task.notice ? `<div class="note" title="${escAttr(task.notice)}">${escText(task.notice)}</div>` : ""}
    ${chips.length ? `<div class="chips">${chips.join("")}</div>` : ""}
  `;
  const mergeExisting = button.querySelector("[data-merge]");
  const keepHot = button.classList.contains("is-graph-hot");
  const keepSelected = button.classList.contains("is-selected");
  button.innerHTML = body;
  // innerHTML teardown can fire pointerout and clear highlight classes — restore them.
  if (keepHot) button.classList.add("is-graph-hot");
  if (keepSelected) button.classList.add("is-selected");
  if (lane(task) === "ready") {
    let merge = mergeExisting;
    if (!merge) {
      const row = document.createElement("div");
      row.className = "merge-row";
      merge = document.createElement("button");
      merge.className = "btn secondary merge-btn";
      merge.type = "button";
      merge.innerHTML = `<span class="merge-label">Copy merge command</span>`;
      row.appendChild(merge);
      button.appendChild(row);
    } else {
      const row = merge.closest(".merge-row") || merge.parentElement;
      if (row) button.appendChild(row);
    }
    merge.setAttribute("data-merge", mergeCmd);
    if (!merge.querySelector(".merge-label") && !merge.classList.contains("copied")) {
      merge.innerHTML = `<span class="merge-label">Copy merge command</span>`;
    }
  }
  return button;
}

function renderCard(task) {
  return fillCard(document.createElement("button"), task);
}

function handleCardClick(event) {
  const card = event.target.closest?.(".card[data-task-id]");
  if (!card) return;
  const task = (state.tasks || []).find((item) => String(item.id) === String(card.dataset.taskId));
  if (!task) return;
  const more = event.target.closest("[data-show-more]");
  if (more) {
    event.stopPropagation();
    select(task, true);
    const proofTab = document.querySelector('.drawer .tab[data-pane="proofpane"]');
    if (proofTab) proofTab.click();
    return;
  }
  const link = event.target.closest("[data-pr-url]");
  if (link) {
    event.stopPropagation();
    const url = link.getAttribute("data-pr-url");
    if (url) window.open(url, "_blank", "noopener");
    return;
  }
  const merge = event.target.closest("[data-merge]");
  if (merge) {
    event.stopPropagation();
    const cmd = merge.getAttribute("data-merge");
    if (cmd && navigator.clipboard) navigator.clipboard.writeText(cmd);
    merge.classList.add("copied");
    merge.innerHTML = `<span class="check" aria-hidden="true">✓</span><span class="merge-label">Copied</span>`;
    setTimeout(() => {
      merge.classList.remove("copied");
      merge.innerHTML = `<span class="merge-label">Copy merge command</span>`;
    }, 2000);
    return;
  }
  select(task, true);
}

function planStepsHtml(awaiting) {
  return (awaiting || [])
    .map(
      (task, i) =>
        `<li class="plan-step" role="presentation" title="${escAttr(task.title)}"><span class="plan-num">${i + 1}.</span> <span class="plan-step-text">${escText(
          task.title,
        )}</span></li>`,
    )
    .join("");
}

function bindPlanActions(card, runId) {
  const ok = card.querySelector(".plan-approve");
  const cancel = card.querySelector(".plan-cancel");
  if (ok) {
    ok.onclick = () => {
      optimistic = { stage: "fleet", awaiting: null, starting: true };
      post(`/api/runs/${runId}/approve`);
      updateRunStrip(state, lastCounts);
    };
  }
  if (cancel) {
    cancel.onclick = () => post(`/api/runs/${runId}/cancel`);
  }
}

function renderPlanCard(awaiting, runId) {
  const card = document.createElement("div");
  card.className = "card plan";
  card.dataset.planCard = "1";
  card.setAttribute("role", "listitem");
  card.innerHTML = `<div class="plan-head"><span class="dot needs" aria-hidden="true"></span>Plan ready</div><ol class="plan-steps">${planStepsHtml(
    awaiting,
  )}</ol>`;
  const actions = document.createElement("div");
  actions.className = "actions plan-actions";
  const ok = document.createElement("button");
  ok.className = "btn primary plan-approve";
  ok.type = "button";
  ok.textContent = "Approve plan";
  const cancel = document.createElement("button");
  cancel.className = "btn ghost plan-cancel";
  cancel.type = "button";
  cancel.textContent = "Cancel";
  actions.appendChild(ok);
  actions.appendChild(cancel);
  card.appendChild(actions);
  bindPlanActions(card, runId);
  return card;
}

function syncPlanCard(card, awaiting, runId) {
  const ol = card.querySelector(".plan-steps");
  if (ol) ol.innerHTML = planStepsHtml(awaiting);
  bindPlanActions(card, runId);
  return card;
}

function ensureEmptyLane(stack) {
  if (!stack) return;
  let empty = stack.querySelector(".lane-empty");
  const hasCards = stack.querySelector(".card, .card.plan");
  if (hasCards) {
    if (empty) empty.remove();
    return;
  }
  if (!empty) {
    empty = document.createElement("div");
    empty.className = "lane-empty";
    empty.innerHTML = `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#icon-inbox"></use></svg><span class="type-caption">No tasks</span>`;
    stack.appendChild(empty);
  }
}

function ensureSkeletons() {
  ["working", "needs", "review", "ready"].forEach((id) => {
    const stack = document.getElementById(id);
    if (!stack || stack.querySelector(".card, .skeleton-card, .lane-empty")) return;
    for (let i = 0; i < 2; i += 1) {
      const sk = document.createElement("div");
      sk.className = "skeleton skeleton-card";
      sk.setAttribute("aria-hidden", "true");
      stack.appendChild(sk);
    }
  });
}

function clearSkeletons() {
  document.querySelectorAll(".skeleton-card").forEach((el) => el.remove());
}

function setEmpty(tableId, emptyId, count) {
  const table = document.getElementById(tableId);
  const empty = document.getElementById(emptyId);
  if (!table || !empty) return;
  table.hidden = count === 0;
  empty.hidden = count !== 0;
}

function select(task, fromUser) {
  if (fromUser && selected && String(selected.id) === String(task.id) && pinned) {
    showDrawerEmpty();
    return;
  }
  if (fromUser) pinned = true;
  if (isMobileBoard() && !fromUser) {
    showDrawerEmpty();
    return;
  }
  if (selected && selected.id === task.id && !fromUser) {
    selected = task;
    document.querySelectorAll(".card[data-task-id]").forEach((el) => {
      el.classList.toggle("is-selected", el.dataset.taskId === task.id);
    });
    if (drawer) drawer.refresh(task, state);
    return;
  }
  const swapping = !!(selected && pinned && fromUser);
  selected = task;
  document.querySelectorAll(".card.is-selected").forEach((el) => el.classList.remove("is-selected"));
  document.querySelectorAll(".card[data-task-id]").forEach((el) => {
    if (el.dataset.taskId === task.id) el.classList.add("is-selected");
  });
  syncSheet(true);
  if (drawer) drawer.show(task, state, { fade: swapping, keepTab: swapping });
}

function openEvents(runId) {
  if (!runId || streamFor === runId || !window.fetch) return;
  streamFor = runId;
  // Decision (15.D5b): keep SSE for state.events, dedupe by byte-offset id (seq is not
  // unique across workers). Do not merge SSE into task.lines — the 1s /api/board poll
  // already rebuilds lines via tasks_from_snapshot; merging both paths double-counted
  // when poll events lacked offsets.
  seedLiveFromBoard(state.events);
  fetch(`/api/runs/${runId}/events`, { headers: auth() })
    .then((res) => {
      if (!res.body) return;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      function pump() {
        reader
          .read()
          .then((chunk) => {
            if (chunk.done) return;
            buf += decoder.decode(chunk.value, { stream: true });
            const parts = buf.split("\n\n");
            buf = parts.pop();
            parts.forEach((block) => {
              const idLine = block.split("\n").find((item) => item.startsWith("id: "));
              const dataLine = block.split("\n").find((item) => item.startsWith("data: "));
              if (!dataLine) return;
              let event;
              try {
                event = JSON.parse(dataLine.slice(6));
              } catch {
                return;
              }
              const offset =
                idLine && /^\d+$/.test(idLine.slice(4).trim())
                  ? Number(idLine.slice(4).trim())
                  : event.offset != null
                    ? Number(event.offset)
                    : null;
              if (offset != null && !Number.isNaN(offset)) event.offset = offset;
              state.events = state.events || [];
              const fp = eventFingerprint(event);
              const knownByOffset = event.offset != null && liveOffsets.has(event.offset);
              const knownByFp = liveFingerprints.has(fp);
              if (knownByOffset || knownByFp) {
                if (event.offset != null) liveOffsets.add(event.offset);
                liveFingerprints.add(fp);
                return;
              }
              if (event.offset != null) liveOffsets.add(event.offset);
              liveFingerprints.add(fp);
              state.events.push(event);
              render(state);
            });
            pump();
          })
          .catch(() => {});
      }
      pump();
    })
    .catch(() => {});
}

let selectedRun = "";

function post(url, body) {
  return fetch(url, {
    method: "POST",
    headers: auth({ "Content-Type": "application/json", "X-TreeFleet-Client": "ui" }),
    body: body ? JSON.stringify(body) : "{}",
  });
}

function applyMobileLane() {
  const runId = state.run_id || "";
  if (runId && mobileLaneByRun.has(runId)) mobileLane = mobileLaneByRun.get(runId);
  document.querySelectorAll(".lane").forEach((el) => {
    el.classList.toggle("is-active", el.id === `lane-${mobileLane}`);
  });
  document.querySelectorAll(".mobile-lane-tabs button").forEach((btn) => {
    btn.setAttribute("aria-selected", String(btn.dataset.lane === mobileLane));
  });
}

function syncLaneRails() {
  const strip = document.querySelector(".lanes");
  if (!strip || isMobileBoard() || strip.classList.contains("is-board-empty")) {
    document.querySelectorAll(".lane.is-rail").forEach((laneEl) => {
      laneEl.classList.remove("is-rail");
      laneEl.tabIndex = -1;
      laneEl.removeAttribute("role");
      laneEl.removeAttribute("aria-label");
    });
    strip?.classList.remove("is-cramped");
    return;
  }
  const lanes = [...document.querySelectorAll(".lane")];
  const anyCards = lanes.some((laneEl) => laneEl.querySelector(".stack .card"));
  // Rails only when at least one lane has cards (not an entirely empty board).
  if (!anyCards) {
    lanes.forEach((laneEl) => {
      laneEl.classList.remove("is-rail");
      laneEl.tabIndex = -1;
      laneEl.removeAttribute("role");
      laneEl.removeAttribute("aria-label");
    });
    strip.classList.remove("is-cramped");
    return;
  }
  // Measure fit with rails off so collapse state does not oscillate.
  lanes.forEach((laneEl) => laneEl.classList.remove("is-rail"));
  const cramped = strip.scrollWidth > strip.clientWidth + 4;
  strip.classList.toggle("is-cramped", cramped);
  lanes.forEach((laneEl) => {
    const id = laneEl.dataset.lane || laneEl.id.replace(/^lane-/, "");
    const stack = laneEl.querySelector(".stack");
    const hasCards = !!(stack && stack.querySelector(".card"));
    const expanded = laneRailExpanded.has(id);
    const asRail = cramped && !hasCards && !expanded;
    laneEl.classList.toggle("is-rail", asRail);
    laneEl.tabIndex = asRail ? 0 : -1;
    if (asRail) {
      laneEl.setAttribute("role", "button");
      laneEl.setAttribute(
        "aria-label",
        `Expand ${laneEl.querySelector(".lane-label")?.textContent || id} lane`,
      );
    } else {
      laneEl.removeAttribute("role");
      laneEl.removeAttribute("aria-label");
    }
  });
}

function expandLaneRail(laneEl) {
  const id = laneEl.dataset.lane || laneEl.id.replace(/^lane-/, "");
  laneRailExpanded.add(id);
  laneEl.classList.remove("is-rail");
  syncLaneRails();
}

let cardKeysBound = false;
function bindCardKeyboard() {
  if (cardKeysBound) return;
  cardKeysBound = true;
  document.querySelector(".lanes")?.addEventListener("click", handleCardClick);
  document.addEventListener("keydown", (event) => {
    const card = event.target.closest?.(".card[data-task-id]");
    if (!card) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      card.click();
      return;
    }
    if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const laneEl = card.closest(".lane");
    const cards = [...(laneEl?.querySelectorAll(".card[data-task-id]") || [])];
    const idx = cards.indexOf(card);
    if (event.key === "ArrowUp" && idx > 0) {
      cards[idx - 1].tabIndex = 0;
      card.tabIndex = -1;
      cards[idx - 1].focus();
      cardFocusId = cards[idx - 1].dataset.taskId;
      return;
    }
    if (event.key === "ArrowDown" && idx < cards.length - 1) {
      cards[idx + 1].tabIndex = 0;
      card.tabIndex = -1;
      cards[idx + 1].focus();
      cardFocusId = cards[idx + 1].dataset.taskId;
      return;
    }
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      const lanes = [...document.querySelectorAll(".lane:not(.is-rail)")];
      const li = lanes.indexOf(laneEl);
      const nextLane = lanes[li + (event.key === "ArrowRight" ? 1 : -1)];
      if (!nextLane) return;
      const target = nextLane.querySelector(".card[data-task-id]");
      if (!target) return;
      card.tabIndex = -1;
      target.tabIndex = 0;
      target.focus();
      cardFocusId = target.dataset.taskId;
    }
  });
  document.addEventListener("keydown", (event) => {
    const rail = event.target.closest?.(".lane.is-rail");
    if (!rail) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      expandLaneRail(rail);
    }
  });
  document.querySelector(".lanes")?.addEventListener("click", (event) => {
    const rail = event.target.closest(".lane.is-rail");
    if (rail) expandLaneRail(rail);
  });
}

function render(data) {
  captureFlip();
  state = data;
  if (data.replay || replay) {
    document.getElementById("replay").hidden = false;
    baseTitle = "TreeFleet replay";
    const liveTab = document.querySelector('[data-pane="live"]');
    if (liveTab) liveTab.textContent = "Recorded output";
    document.getElementById("run").disabled = true;
  }
  const project = (data.repo || "TreeFleet").split(/[\\/]/).pop();
  setSafeText(document.getElementById("path"), data.repo || "");
  setSafeText(document.getElementById("title"), project);
  setSafeText(document.getElementById("side-project"), project);
  setSafeText(
    document.getElementById("ref"),
    `${sanitizeBidi(data.branch || "")}${data.head ? ` @ ${data.head}` : ""}`
  );
  document.getElementById("clean").textContent = data.clean ? "Main checkout clean" : "Main checkout dirty";
  document.getElementById("stage").textContent = `Stage: ${(optimistic && optimistic.stage) || data.stage || "guard"}`;
  if (data.started_at) started = data.started_at;
  if (data.finished_at) finished = data.finished_at;
  else if (data.stage === "done" && !finished) finished = Date.now() / 1000;
  if (optimistic && data.stage === "fleet" && !data.awaiting) optimistic = null;

  renderRunHistory(data);
  syncComposerEnabled(data);
  autosizeComposer(document.getElementById("goal"));
  autosizeComposer(document.getElementById("follow-goal"));

  const tasks = data.tasks || [];
  const committedKey = (data.worktrees || [])
    .map((row) => {
      const files = Array.isArray(row.committed) ? row.committed : [];
      const bits = files.map((file) => `${file.path}:${file.additions || 0}:${file.deletions || 0}`).join(",");
      const working = Array.isArray(row.files)
        ? row.files.map((file) => `${file.path}:${file.additions || 0}:${file.deletions || 0}`).join(",")
        : "";
      return `${row.branch || ""}=${bits}|${row.head || ""}|${working}`;
    })
    .join("|");
  const laneKey = JSON.stringify({ awaiting: data.awaiting, tasks, committedKey });
  clearSkeletons();
  boardReady = true;
  const counts = setLaneCounts(tasks, !!(optimistic && optimistic.awaiting === null ? null : data.awaiting));
  lastCounts = counts;
  notifyHuman(needsCount(counts));
  updateRunStrip(data, counts);
  const focusBefore = document.activeElement;
  const focusTaskId =
    focusBefore && focusBefore.classList && focusBefore.classList.contains("card")
      ? focusBefore.dataset.taskId
      : cardFocusId;
  const scrollLeft = document.querySelector(".lanes")?.scrollLeft || 0;
  const laneScrolls = {};
  document.querySelectorAll(".lane .stack").forEach((stack) => {
    laneScrolls[stack.id] = stack.scrollTop;
  });

  const byLane = { needs: [], working: [], review: [], ready: [], done: [] };
  tasks.forEach((task) => {
    const id = lane(task);
    if (!byLane[id]) byLane[id] = [];
    byLane[id].push(task);
  });

  Object.keys(byLane).forEach((id) => {
    const stack = document.getElementById(id);
    if (!stack) return;
    const wanted = new Set(byLane[id].map((task) => String(task.id)));
    if (id === "needs" && data.awaiting) wanted.add("__plan__");
    [...stack.querySelectorAll(".card[data-task-id], .card.plan")].forEach((el) => {
      const key = el.dataset.planCard ? "__plan__" : el.dataset.taskId;
      if (!wanted.has(key)) el.remove();
    });
    if (id === "needs" && data.awaiting) {
      let plan = stack.querySelector(".card.plan");
      if (plan) syncPlanCard(plan, data.awaiting, data.run_id);
      else stack.insertBefore(renderPlanCard(data.awaiting, data.run_id), stack.firstChild);
    } else {
      const plan = stack.querySelector(".card.plan");
      if (plan) plan.remove();
    }
    byLane[id].forEach((task) => {
      const existing = stack.querySelector(`.card[data-task-id="${CSS.escape(String(task.id))}"]`);
      if (existing) fillCard(existing, task);
      else {
        const next = renderCard(task);
        next.tabIndex = -1;
        stack.appendChild(next);
      }
    });
    ensureEmptyLane(stack);
    if (laneScrolls[id] != null) stack.scrollTop = laneScrolls[id];
  });

  const boardEmpty = document.getElementById("board-empty");
  const hasAnyCards = tasks.length > 0 || !!data.awaiting;
  if (boardEmpty) boardEmpty.hidden = hasAnyCards;
  document.querySelector(".lanes")?.classList.toggle("is-board-empty", !hasAnyCards);

  if (laneKey !== laneSig) {
    laneSig = laneKey;
    playFlip();
    if (pinned && selected) {
      const held = tasks.find((task) => task.id === selected.id);
      if (held) {
        selected = held;
        if (drawer) drawer.refresh(held, state);
      } else showDrawerEmpty();
    } else if (!pinned) {
      showDrawerEmpty();
    }
  } else if (selected && drawer) {
    const held = tasks.find((task) => task.id === selected.id);
    if (held) {
      selected = held;
      drawer.refresh(held, state);
    }
  }

  const lanesEl = document.querySelector(".lanes");
  if (lanesEl) lanesEl.scrollLeft = scrollLeft;
  if (focusTaskId) {
    const again = document.querySelector(`.card[data-task-id="${CSS.escape(focusTaskId)}"]`);
    if (again && focusBefore && document.body.contains(again)) {
      again.focus({ preventScroll: true });
      cardFocusId = focusTaskId;
    }
  }
  syncLaneRails();
  applyMobileLane();
  bindCardKeyboard();

  const body = document.querySelector("#worktrees tbody");
  body.innerHTML = "";
  const worktrees = data.worktrees || [];
  worktrees.forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class='mono'>${escText(row.path)}</td><td class='mono'>${escText(
      row.branch,
    )}</td><td>${escText(changedLabel(row))}</td>`;
    body.appendChild(tr);
  });
  document.getElementById("count-worktrees").textContent = String(worktrees.length);
  setEmpty("worktrees", "empty-worktrees", worktrees.length);
  if (graph) graph.draw(data);
  if (data.run_id && data.run_id !== streamFor) {
    liveOffsets = new Set();
    liveFingerprints = new Set();
    streamFor = "";
  }
  seedLiveFromBoard(data.events);
  if (data.run_id) openEvents(data.run_id);

  const guards = document.getElementById("guards");
  guards.innerHTML = "";
  const guardEvents = (data.events || []).filter(
    (event) => event.type === "guard.decision" && !argumentFailure(event.payload),
  );
  guardEvents.forEach((event) => {
    const payload = event.payload || {};
    const p = document.createElement("p");
    const reasons = (payload.reasons || []).join("; ");
    const scores = payload.scores || {};
    const scoreText = Object.keys(scores).length ? JSON.stringify(scores) : "";
    p.textContent = [payload.stage || "", payload.allowed ? "allowed" : "blocked", reasons, scoreText]
      .filter(Boolean)
      .join(" ");
    guards.appendChild(p);
  });
  document.getElementById("count-guards").textContent = String(guardEvents.length);
  document.getElementById("empty-guards").hidden = guardEvents.length !== 0;
  guards.hidden = guardEvents.length === 0;

  const proofBody = document.querySelector("#proof tbody");
  proofBody.innerHTML = "";
  const proofs = (data.report && data.report.isolation && data.report.isolation.tasks) || data.proof || [];
  proofs.forEach((item) => {
    const tr = document.createElement("tr");
    const mark = (value) => (value ? "yes" : "NO");
    tr.innerHTML = `<td>${escText(item.task_id || "")}</td><td>${mark(item.process_cwd_matches)}</td><td>${mark(
      item.toplevel_matches,
    )}</td><td>${mark(item.registered_in_worktree_list)}</td><td>${mark(item.canary_absent)}</td><td>${mark(
      item.changes_within_allowed_paths,
    )}</td>`;
    proofBody.appendChild(tr);
  });
  document.getElementById("count-proof").textContent = String(proofs.length);
  setEmpty("proof", "empty-proof", proofs.length);

  const integration = document.getElementById("integration");
  const merged = data.report && data.report.integration;
  integration.textContent = merged
    ? `Branch ${merged.branch || ""} checks ${(merged.checks || [])
        .map((c) => `${c.name}${c.passed ? " pass" : " fail"}`)
        .join(", ") || "none"}`
    : "No merge yet.";
  document.getElementById("count-integration").textContent = merged ? "1" : "0";

  const costBody = document.querySelector("#metrics tbody");
  costBody.innerHTML = "";
  const costs = (data.report && data.report.metrics && data.report.metrics.costs) || [];
  costs.forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${escText(costRole(row))}</td><td>${escText(row.model)}</td><td>${moneyCell(row)}</td>`;
    costBody.appendChild(tr);
  });
  document.getElementById("count-metrics").textContent = String(costs.length);
  setEmpty("metrics", "empty-metrics", costs.length);

  if (data.message) {
    document.getElementById("error").hidden = false;
    document.getElementById("error").textContent = humanizeStatus(data.message);
  }
  if (orch && view === "orchestrator") orch.renderThread(data);
}

function tick() {
  if (!started) return;
  const shown = elapsedText();
  document.getElementById("elapsed").textContent = shown;
  document.getElementById("metrics-elapsed").textContent = `Elapsed ${shown}`;
  const summary = document.getElementById("summary-elapsed");
  if (summary) summary.textContent = shown;
  document.querySelectorAll(".card .age").forEach((el) => {
    if (el.dataset.fixed !== "1") el.textContent = shown;
  });
  if (state && Object.keys(state).length) {
    updateRunStrip(state, lastCounts);
  }
}

function refresh() {
  const query = selectedRun ? `?run=${encodeURIComponent(selectedRun)}` : "";
  fetch(`/api/board${query}`, { headers: auth() })
    .then((res) => res.json())
    .then(render)
    .catch(() => {});
  fetch("/api/worktrees", { headers: auth() })
    .then((res) => res.json())
    .then((data) => {
      if (!state.worktrees) state.worktrees = [];
      state.worktrees = data.worktrees || [];
      const body = document.querySelector("#worktrees tbody");
      body.innerHTML = "";
      state.worktrees.forEach((row) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td class='mono'>${escText(row.path)}</td><td class='mono'>${escText(
          row.branch,
        )}</td><td>${escText(changedLabel(row))}</td>`;
        body.appendChild(tr);
      });
      document.getElementById("count-worktrees").textContent = String(state.worktrees.length);
      setEmpty("worktrees", "empty-worktrees", state.worktrees.length);
      if (graph && (state.tasks || state.head)) graph.draw(state);
    })
    .catch(() => {});
}

function setView(next) {
  view = next;
  document.getElementById("view-kanban").hidden = next !== "kanban";
  document.getElementById("view-orchestrator").hidden = next !== "orchestrator";
  document.getElementById("new-run").hidden = next !== "kanban";
  document.querySelectorAll('.view-toggle [role="tab"]').forEach((tab) => {
    tab.setAttribute("aria-selected", String(tab.dataset.view === next));
  });
  if (next === "orchestrator" && orch) orch.renderThread(state);
}

document.getElementById("auto").addEventListener("click", () => {
  const button = document.getElementById("auto");
  button.setAttribute("aria-checked", String(button.getAttribute("aria-checked") !== "true"));
});

document.getElementById("run-settings").addEventListener("click", () => {
  const pop = document.getElementById("settings-popover");
  const open = pop.hidden;
  pop.hidden = !open;
  document.getElementById("run-settings").setAttribute("aria-expanded", String(open));
});

document.addEventListener("click", (event) => {
  const wrap = document.querySelector(".settings-wrap");
  if (wrap && !wrap.contains(event.target)) {
    document.getElementById("settings-popover").hidden = true;
    document.getElementById("run-settings").setAttribute("aria-expanded", "false");
  }
});

document.querySelectorAll(".details-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".details-tab").forEach((item) => {
      item.setAttribute("aria-selected", String(item === tab));
    });
    document.querySelectorAll(".details-pane").forEach((pane) => {
      pane.hidden = pane.id !== `pane-${tab.dataset.details}`;
    });
  });
});

function submitNewRun() {
  const goalEl = document.getElementById("goal");
  const goal = String((goalEl && goalEl.value) || "");
  if (!goal.trim() || replay) return;
  const auto = document.getElementById("auto").getAttribute("aria-checked") === "true";
  const err = document.getElementById("error");
  if (err) {
    err.hidden = true;
    err.textContent = "";
  }
  setRunLoading(true);
  post("/api/runs", {
    goal,
    worker_model: document.getElementById("model").value,
    plan_approval: !auto,
  })
    .then((res) => res.json().then((data) => ({ ok: res.ok, data })))
    .then(({ ok, data }) => {
      setRunLoading(false);
      if (!ok || data.error) {
        if (err) {
          err.hidden = false;
          err.textContent = data.error || "Could not start run";
        }
        return;
      }
      started = Date.now() / 1000;
      selectedRun = data.run_id || "";
      refresh();
    })
    .catch(() => {
      setRunLoading(false);
      if (err) {
        err.hidden = false;
        err.textContent = "Could not start run";
      }
    });
}

document.getElementById("run").addEventListener("click", () => submitNewRun());

document.getElementById("new-task").addEventListener("click", () => {
  setView("kanban");
  document.getElementById("goal").focus();
});

const overflowNew = document.getElementById("overflow-new-task");
if (overflowNew) {
  overflowNew.addEventListener("click", () => {
    document.getElementById("topbar-more-menu").hidden = true;
    document.getElementById("topbar-more").setAttribute("aria-expanded", "false");
    document.getElementById("new-task").click();
  });
}

document.getElementById("topbar-more")?.addEventListener("click", () => {
  const menu = document.getElementById("topbar-more-menu");
  const btn = document.getElementById("topbar-more");
  const open = menu.hidden;
  menu.hidden = !open;
  btn.setAttribute("aria-expanded", String(open));
});

function bindComposerField(textareaId, runBtnId, submitFn) {
  const area = document.getElementById(textareaId);
  if (!area) return;
  area.addEventListener("input", () => {
    autosizeComposer(area);
    syncComposerEnabled(state);
  });
  area.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      if (submitFn) submitFn();
      else document.getElementById(runBtnId)?.click();
    }
  });
}

bindComposerField("goal", "run", submitNewRun);
bindComposerField("follow-goal", "follow-run");

const sidebarSearch = document.getElementById("sidebar-search");
if (sidebarSearch) {
  sidebarSearch.addEventListener("click", () => {
    if (palette) palette.open();
  });
}

function setSidebarOpen(open) {
  document.querySelector(".shell")?.classList.toggle("sidebar-open", open);
  document.getElementById("sidebar")?.classList.toggle("is-sheet-open", open);
  const scrim = document.getElementById("sidebar-scrim");
  if (scrim) scrim.hidden = !open;
}

document.getElementById("sidebar-toggle")?.addEventListener("click", () => {
  const open = !document.querySelector(".shell")?.classList.contains("sidebar-open");
  setSidebarOpen(open);
});
document.getElementById("sidebar-scrim")?.addEventListener("click", () => setSidebarOpen(false));

document.getElementById("run-history")?.addEventListener("keydown", (event) => {
  const buttons = [...document.querySelectorAll("#run-history button")];
  if (!buttons.length) return;
  const active = document.activeElement;
  const idx = buttons.indexOf(active);
  if (event.key === "ArrowDown") {
    event.preventDefault();
    buttons[Math.min(buttons.length - 1, Math.max(0, idx) + 1)]?.focus();
  } else if (event.key === "ArrowUp") {
    event.preventDefault();
    buttons[Math.max(0, (idx < 0 ? 0 : idx) - 1)]?.focus();
  } else if (event.key === "Enter" && idx >= 0) {
    event.preventDefault();
    buttons[idx].click();
  }
});

document.querySelectorAll('.view-toggle [role="tab"]').forEach((tab) => {
  tab.addEventListener("click", () => setView(tab.dataset.view));
});

document.querySelectorAll(".mobile-lane-tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    mobileLane = btn.dataset.lane;
    if (state.run_id) mobileLaneByRun.set(state.run_id, mobileLane);
    applyMobileLane();
  });
});
document.querySelector(".mobile-lane-tabs")?.addEventListener("click", (event) => {
  const btn = event.target.closest("button[data-lane]");
  if (!btn) return;
  mobileLane = btn.dataset.lane;
  if (state.run_id) mobileLaneByRun.set(state.run_id, mobileLane);
  applyMobileLane();
});

drawer = createDrawer({ auth, worktreeFor });
drawer.bind();

graph = createGitGraph({
  onSelect: (task) => select(task, true),
});

function mergeCommand() {
  const integration = state.report && state.report.integration;
  const branch = (integration && integration.branch) || "";
  return branch ? `git merge ${branch}` : "";
}

palette = createPalette({
  onNewRun: () => {
    setView("kanban");
    document.getElementById("goal").focus();
  },
  onJumpTask: (task) => select(task, true),
  onSwitchView: () => setView(view === "kanban" ? "orchestrator" : "kanban"),
  onToggleAuto: () => document.getElementById("auto").click(),
  onCopyMerge: () => {
    const cmd = mergeCommand();
    if (cmd && navigator.clipboard) navigator.clipboard.writeText(cmd);
  },
  getTasks: () => state.tasks || [],
});
palette.bind();

orch = createOrchestrator({
  clock,
  post,
  selectTask: select,
  auth,
  onPlanApproved: () => {
    optimistic = { stage: "fleet", awaiting: null, starting: true };
    if (state) {
      state.awaiting = null;
      state.stage = "fleet";
      updateRunStrip(state, lastCounts);
      if (view === "orchestrator") orch.renderThread(state);
    }
  },
});
orch.bind();

document.getElementById("drawer-close").addEventListener("click", () => showDrawerEmpty());
document.getElementById("drawer-grab").addEventListener("click", () => showDrawerEmpty());
document.getElementById("drawer-backdrop").addEventListener("click", () => showDrawerEmpty());

(function bindDrawerResize() {
  applyDrawerWidth(readDrawerWidth());
  const handle = document.getElementById("drawer-resize");
  if (!handle) return;
  let dragging = false;
  let startX = 0;
  let startW = 480;
  const onMove = (event) => {
    if (!dragging) return;
    const dx = startX - event.clientX;
    applyDrawerWidth(startW + dx);
  };
  const onUp = () => {
    if (!dragging) return;
    dragging = false;
    const now =
      Number(getComputedStyle(document.documentElement).getPropertyValue("--drawer-width").replace("px", "")) || 480;
    writeDrawerWidth(now);
    syncLaneRails();
    window.removeEventListener("pointermove", onMove);
    window.removeEventListener("pointerup", onUp);
  };
  handle.addEventListener("pointerdown", (event) => {
    if (isMobileBoard()) return;
    dragging = true;
    startX = event.clientX;
    startW = readDrawerWidth();
    handle.setPointerCapture?.(event.pointerId);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  });
  handle.addEventListener("dblclick", () => {
    applyDrawerWidth(480);
    writeDrawerWidth(480);
    syncLaneRails();
  });
  handle.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const cur = readDrawerWidth();
    const next = applyDrawerWidth(cur + (event.key === "ArrowLeft" ? 16 : -16));
    writeDrawerWidth(next);
    syncLaneRails();
  });
})();

document.getElementById("branch-copy")?.addEventListener("click", async () => {
  const meta = document.getElementById("task-meta");
  const full = meta?.getAttribute("data-branch") || meta?.getAttribute("title") || meta?.textContent || "";
  const btn = document.getElementById("branch-copy");
  try {
    if (full && navigator.clipboard) await navigator.clipboard.writeText(full);
    if (btn) {
      btn.title = "Copied";
      btn.setAttribute("aria-label", "Copied");
      setTimeout(() => {
        btn.title = "Copy branch";
        btn.setAttribute("aria-label", "Copy branch");
      }, 1500);
    }
  } catch (_err) {
    /* ignore */
  }
});

(function bindDrawerSwipe() {
  const drawerEl = document.querySelector(".drawer");
  if (!drawerEl) return;
  let startY = 0;
  drawerEl.addEventListener(
    "touchstart",
    (event) => {
      if (!drawerEl.classList.contains("is-open")) return;
      startY = event.touches[0].clientY;
    },
    { passive: true },
  );
  drawerEl.addEventListener(
    "touchend",
    (event) => {
      if (!drawerEl.classList.contains("is-open")) return;
      const dy = event.changedTouches[0].clientY - startY;
      if (dy > 64) showDrawerEmpty();
    },
    { passive: true },
  );
})();

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (palette && palette.isOpen()) {
    palette.close();
    return;
  }
  showDrawerEmpty();
});

fetch("/api/models", { headers: auth() })
  .then((res) => res.json())
  .then((models) => {
    const select = document.getElementById("model");
    models.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.label;
      select.appendChild(option);
    });
    orch.syncModels(models);
  });

if (replay) document.getElementById("replay").hidden = false;
applyDrawerWidth(readDrawerWidth());
syncSheet(false);
applyMobileLane();
syncComposerEnabled(state);
autosizeComposer(document.getElementById("goal"));
autosizeComposer(document.getElementById("follow-goal"));
ensureSkeletons();
bindCardKeyboard();
window.addEventListener("resize", () => {
  laneRailExpanded.clear();
  syncLaneRails();
  if (selected) syncSheet(true);
  else syncSheet(false);
});
refresh();
setInterval(refresh, 1000);
setInterval(tick, 1000);
setInterval(refreshRelativeTimes, 30000);
