import { createOrchestrator } from "./orchestrator.js";
import { costRole, createDrawer } from "./drawer.js";
import { createGitGraph } from "./git-graph.js";
import { createPalette } from "./palette.js";

const token = document.querySelector('meta[name="tf-token"]').content;
const replay = document.documentElement.dataset.replay === "true";
const STAGES = ["guard", "plan", "approve", "fleet", "review", "integrate"];
let started = 0;
let finished = 0;
let selected = null;
let pinned = false;
let state = {};
let streamFor = "";
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

function esc(value) {
  return String(value == null ? "" : value).replace(/[&<>"']/g, (ch) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch],
  );
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

function lane(task) {
  return task.lane || "working";
}

function isRunning(task) {
  return lane(task) === "working" && !!task.action && task.status !== "done" && task.status !== "failed";
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
    document.title = `(${count}) ${baseTitle}`;
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
    document.title = baseTitle;
  }
  lastNeedsHuman = needs;
}

function elapsedText() {
  if (!started) return "0:00";
  return clock((finished || Date.now() / 1000) - started);
}

function isMobileBoard() {
  return window.matchMedia("(max-width: 1100px)").matches;
}

function syncSheet(taskOpen) {
  const drawerEl = document.querySelector(".drawer");
  const backdrop = document.getElementById("drawer-backdrop");
  const close = document.getElementById("drawer-close");
  const mobile = isMobileBoard();
  if (drawerEl) drawerEl.classList.toggle("is-open", !!(taskOpen && mobile));
  if (backdrop) backdrop.hidden = !(taskOpen && mobile);
  if (close) close.hidden = !taskOpen;
}

function showDrawerEmpty() {
  selected = null;
  pinned = false;
  document.querySelectorAll(".card.is-selected").forEach((el) => el.classList.remove("is-selected"));
  syncSheet(false);
  document.getElementById("task-title").textContent = "No task selected";
  document.getElementById("task-meta").textContent = "";
  document.getElementById("drawer-empty").hidden = false;
  document.getElementById("drawer-task").hidden = true;
  document.getElementById("summary-stage").textContent = (optimistic && optimistic.stage) || state.stage || "guard";
  document.getElementById("summary-working").textContent = String(lastCounts.working || 0);
  document.getElementById("summary-needs").textContent = String(lastCounts.needs || 0);
  document.getElementById("summary-review").textContent = String(lastCounts.review || 0);
  document.getElementById("summary-ready").textContent = String(lastCounts.ready || 0);
  document.getElementById("summary-elapsed").textContent = elapsedText();
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
  if (text === "unpriced" || text === "usage not reported") return `<span class='pill'>${esc(text)}</span>`;
  return esc(text);
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
  return `<svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><rect x="2" y="3" width="12" height="10" rx="2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M5 8h6M5 10.5h4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>`;
}

function gitGlyph() {
  return `<svg width="12" height="12" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="4" r="2" fill="currentColor"/><circle cx="8" cy="12" r="2" fill="currentColor"/><path d="M8 6v4" stroke="currentColor" stroke-width="1.5"/></svg>`;
}

function middleTruncate(text, max = 32) {
  const value = String(text || "");
  if (value.length <= max) return value;
  const keep = Math.max(4, Math.floor((max - 1) / 2));
  return `${value.slice(0, keep)}…${value.slice(-keep)}`;
}

function taskAge(task) {
  if (task.age_s != null) return clock(task.age_s);
  if (!started) return "";
  const end = finished || Date.now() / 1000;
  return clock(end - started);
}

function setLaneCounts(tasks, awaiting) {
  const counts = { working: 0, needs: awaiting ? 1 : 0, review: 0, ready: 0 };
  tasks.forEach((task) => {
    counts[lane(task)] = (counts[lane(task)] || 0) + 1;
  });
  Object.keys(counts).forEach((id) => {
    const el = document.querySelector(`#lane-${id} .n`);
    if (el) el.textContent = String(counts[id]);
    const tab = document.querySelector(`.mobile-lane-tabs [data-lane="${id}"]`);
    if (tab) tab.textContent = `${tab.dataset.label} ${counts[id]}`;
  });
  return counts;
}

function runStripState(data, counts) {
  const stage = (optimistic && optimistic.stage) || data.stage || "guard";
  const awaiting = optimistic && optimistic.awaiting === null ? null : data.awaiting;
  const total = (data.tasks || []).length || (awaiting ? awaiting.length : 0);
  const active = counts.working || (optimistic && optimistic.starting ? total : 0);
  const elapsed = elapsedText();
  const blocked =
    !(optimistic && optimistic.stage === "fleet") &&
    ((data.message && /blocked/i.test(data.message)) ||
      (data.events || []).some(
        (event) =>
          event.type === "guard.decision" &&
          (event.payload || {}).stage === "input" &&
          (event.payload || {}).allowed === false,
      ));
  if (blocked) {
    const why = (data.message || "").replace(/^input blocked:\s*/i, "") || "input blocked";
    return { line: `Blocked · ${why}`, stage: "guard" };
  }
  if (awaiting) return { line: `Awaiting approval · ${elapsed}`, stage: "approve" };
  if (data.pr_mode && (stage === "done" || stage === "timed_out" || stage === "awaiting_prs" || stage === "pr")) {
    const label = stage === "timed_out" ? "Timed out" : stage === "done" ? "Done" : "PRs open";
    return { line: `${label} · ${elapsed}`, stage: "pr" };
  }
  if (data.pr_mode && (stage === "review" || stage === "integrate")) {
    return { line: `PRs open · ${elapsed}`, stage: "pr" };
  }
  if (stage === "done") return { line: `Done · ${elapsed}`, stage: "integrate" };
  if (stage === "integrate") return { line: `Integrating · ${elapsed}`, stage: "integrate" };
  if (stage === "review") return { line: `Reviewing · ${elapsed}`, stage: "review" };
  if (stage === "fleet" || active || (optimistic && optimistic.starting)) {
    return { line: `Running · ${active || total || 0} of ${total || active || 0} tasks · ${elapsed}`, stage: "fleet" };
  }
  if (stage === "plan") return { line: `Planning · ${elapsed}`, stage: "plan" };
  if (stage === "approve") return { line: `Awaiting approval · ${elapsed}`, stage: "approve" };
  if (stage === "guard") return { line: `Guarding · ${elapsed}`, stage: "guard" };
  return { line: `Idle · ${elapsed}`, stage };
}

function updateRunStrip(data, counts) {
  const info = runStripState(data, counts);
  document.getElementById("run-status").textContent = info.line;
  const shown = elapsedText();
  document.getElementById("elapsed").textContent = shown;
  document.getElementById("metrics-elapsed").textContent = `Elapsed ${shown}`;
  const summary = document.getElementById("summary-elapsed");
  if (summary) summary.textContent = shown;
  const last = document.querySelector('#stage-stepper li[data-stage="integrate"], #stage-stepper li[data-stage="pr"]');
  if (last) {
    last.dataset.stage = data.pr_mode ? "pr" : "integrate";
    last.textContent = data.pr_mode ? "pr" : "integrate";
  }
  const names = [...document.querySelectorAll("#stage-stepper li")].map((item) => item.dataset.stage);
  const idx = names.indexOf(info.stage);
  document.querySelectorAll("#stage-stepper li").forEach((item, i) => {
    item.classList.toggle("is-current", i === idx);
    item.classList.toggle("is-done", idx >= 0 && i < idx);
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
    else text = "Needs a human decision";
  } else if (task.verdict === "approve" || lane(task) === "ready") {
    tone = "ok";
    text = suggestions.length ? "Approved with notes" : "Checks passed";
  }
  return { text: text.slice(0, 60), tone };
}

function renderCard(task) {
  const button = document.createElement("button");
  button.className = "card";
  button.type = "button";
  button.dataset.taskId = task.id;
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
    chips.push(`<span class="chip ${passed ? "pass" : "fail"}">${esc(name)}${passed ? " pass" : " fail"}</span>`);
  });
  if (task.pr_number) {
    chips.push(`<span class="chip pr" data-pr-url="${esc(task.pr_url || "")}">PR #${esc(task.pr_number)}</span>`);
    const failing = task.pr_checks === "failing";
    const passing = task.pr_checks === "passing";
    const label = passing ? "checks passing" : failing ? "checks failing" : "checks pending";
    chips.push(`<span class="chip ${passing ? "pass" : failing ? "fail" : ""}">${label}</span>`);
  }
  if (counts.count) {
    chips.push(
      `<span class="chip lines"><span class="add">+${counts.added}</span> <span class="del">-${counts.removed}</span></span>`,
    );
  }
  if (task.round) chips.push(`<span class="chip round">revise ${esc(task.round)}</span>`);
  const note = reviewNote(task);
  const noteHtml = note.text
    ? `<div class="${note.tone === "needs" ? "reason" : "note"}">${esc(note.text)}</div>`
    : "";
  const age = taskAge(task);
  const action = running
    ? `<div class="action-line"><span class="spinner" aria-hidden="true"></span><span class="action-text">${esc(
        task.action,
      )}</span></div>`
    : "";
  const verdictClass = ["approve", "reject", "revise", "needs_review"].includes(verdictLabel) ? verdictLabel : "";
  const verdict = verdictLabel
    ? `<div class="verdict-row"><span class="chip verdict ${verdictClass}">${esc(verdictLabel.replaceAll("_", " "))}</span></div>${noteHtml}`
    : noteHtml;
  const mergeCmd = task.pr_number
    ? `gh pr merge ${task.pr_number} --repo ${state.pr_repo || ""} --squash`
    : `git -C ${state.repo || ""} merge ${task.branch || task.id}`;
  const mergeBtn =
    lane(task) === "ready"
      ? `<div class="merge-row"><button class="btn ghost merge-btn" type="button" data-merge="${esc(
          mergeCmd,
        )}"><span class="merge-label">Copy merge command</span></button></div>`
      : "";
  button.innerHTML = `
    <div class="model-row">
      <span class="model">${modelIcon()}<span>${esc(task.model || "model")}</span></span>
      <span class="age"${task.age_s != null ? ' data-fixed="1"' : ""}>${esc(age)}</span>
    </div>
    <div class="title">${esc(task.title)}</div>
    <span class="branch mono" title="${esc(task.branch || task.id)}">${gitGlyph()}<span>${esc(
      middleTruncate(task.branch || task.id),
    )}</span></span>
    ${action}
    ${verdict}
    ${chips.length ? `<div class="chips">${chips.join("")}</div>` : ""}
    ${mergeBtn}
  `;
  button.addEventListener("click", (event) => {
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
  });
  return button;
}

function setEmpty(tableId, emptyId, count) {
  const table = document.getElementById(tableId);
  const empty = document.getElementById(emptyId);
  if (!table || !empty) return;
  table.hidden = count === 0;
  empty.hidden = count !== 0;
}

function select(task, fromUser) {
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
  selected = task;
  document.querySelectorAll(".card.is-selected").forEach((el) => el.classList.remove("is-selected"));
  document.querySelectorAll(".card[data-task-id]").forEach((el) => {
    if (el.dataset.taskId === task.id) el.classList.add("is-selected");
  });
  syncSheet(true);
  if (drawer) drawer.show(task, state);
}

function openEvents(runId) {
  if (!runId || streamFor === runId || !window.fetch) return;
  streamFor = runId;
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
              const line = block.split("\n").find((item) => item.startsWith("data: "));
              if (!line) return;
              const event = JSON.parse(line.slice(6));
              state.events = state.events || [];
              if (!state.events.some((item) => item.seq === event.seq)) state.events.push(event);
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

function post(url, body) {
  return fetch(url, {
    method: "POST",
    headers: auth({ "Content-Type": "application/json" }),
    body: body ? JSON.stringify(body) : "{}",
  });
}

function applyMobileLane() {
  document.querySelectorAll(".lane").forEach((el) => {
    el.classList.toggle("is-active", el.id === `lane-${mobileLane}`);
  });
  document.querySelectorAll(".mobile-lane-tabs button").forEach((btn) => {
    btn.setAttribute("aria-selected", String(btn.dataset.lane === mobileLane));
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
  document.getElementById("path").textContent = data.repo || "";
  document.getElementById("title").textContent = project;
  document.getElementById("side-project").textContent = project;
  document.getElementById("ref").textContent = `${data.branch || ""}${data.head ? ` @ ${data.head}` : ""}`;
  document.getElementById("clean").textContent = data.clean ? "Main checkout clean" : "Main checkout dirty";
  document.getElementById("stage").textContent = `Stage: ${(optimistic && optimistic.stage) || data.stage || "guard"}`;
  if (data.started_at) started = data.started_at;
  if (data.finished_at) finished = data.finished_at;
  else if (data.stage === "done" && !finished) finished = Date.now() / 1000;
  if (optimistic && data.stage === "fleet" && !data.awaiting) optimistic = null;

  const runList = document.getElementById("run-history");
  if (runList) {
    runList.innerHTML = "";
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("aria-current", "true");
    const statusDot = data.needs_human || data.awaiting ? "orange" : data.stage === "done" ? "green" : "blue";
    btn.innerHTML = `<span class="dot ${statusDot}"></span><span class="mono">${esc(data.run_id || "idle")}</span>`;
    li.appendChild(btn);
    runList.appendChild(li);
  }

  const tasks = data.tasks || [];
  const committedKey = (data.worktrees || [])
    .map((row) => {
      const files = Array.isArray(row.committed) ? row.committed : [];
      const bits = files.map((file) => `${file.path}:${file.additions || 0}:${file.deletions || 0}`).join(",");
      return `${row.branch || ""}=${bits}`;
    })
    .join("|");
  const laneKey = JSON.stringify({ awaiting: data.awaiting, tasks, committedKey });
  const counts = setLaneCounts(tasks, !!(optimistic && optimistic.awaiting === null ? null : data.awaiting));
  lastCounts = counts;
  notifyHuman(needsCount(counts));
  updateRunStrip(data, counts);
  if (laneKey !== laneSig) {
    laneSig = laneKey;
  ["needs", "working", "review", "ready"].forEach((id) => {
    document.getElementById(id).innerHTML = "";
  });

  if (data.awaiting) {
    const card = document.createElement("div");
    card.className = "card plan";
    const items = data.awaiting
      .map((task) => `<li>${esc(task.title)} · ${esc((task.allowed_paths || []).join(", "))}</li>`)
      .join("");
    card.innerHTML = `<div class="plan-head"><span class="dot orange"></span>Plan ready</div><ul>${items}</ul>`;
    const actions = document.createElement("div");
    actions.className = "actions";
    const ok = document.createElement("button");
    ok.className = "btn primary";
    ok.type = "button";
      ok.textContent = "Approve plan";
      ok.addEventListener("click", () => {
        optimistic = { stage: "fleet", awaiting: null, starting: true };
        post(`/api/runs/${data.run_id}/approve`);
        updateRunStrip(data, lastCounts);
      });
      const cancel = document.createElement("button");
      cancel.className = "btn ghost";
      cancel.type = "button";
      cancel.textContent = "Cancel";
      cancel.addEventListener("click", () => post(`/api/runs/${data.run_id}/cancel`));
    actions.appendChild(ok);
    actions.appendChild(cancel);
    card.appendChild(actions);
    document.getElementById("needs").appendChild(card);
  }

  tasks.forEach((task) => {
    document.getElementById(lane(task)).appendChild(renderCard(task));
  });
  playFlip();

  if (!pinned) {
    let current = null;
    tasks.forEach((task) => {
      if (!current && lane(task) === "working") current = task;
    });
    if (current && !isMobileBoard()) select(current, false);
    else showDrawerEmpty();
  } else if (selected) {
    const held = tasks.find((task) => task.id === selected.id);
    if (held) select(held, !!document.querySelector(".drawer.is-open"));
    else showDrawerEmpty();
  } else {
    showDrawerEmpty();
  }
  }
  applyMobileLane();

  const body = document.querySelector("#worktrees tbody");
  body.innerHTML = "";
  const worktrees = data.worktrees || [];
  worktrees.forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class='mono'>${esc(row.path)}</td><td class='mono'>${esc(
      row.branch,
    )}</td><td>${esc(changedLabel(row))}</td>`;
    body.appendChild(tr);
  });
  document.getElementById("count-worktrees").textContent = String(worktrees.length);
  setEmpty("worktrees", "empty-worktrees", worktrees.length);
  if (graph) graph.draw(data);
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
    tr.innerHTML = `<td>${esc(item.task_id || "")}</td><td>${mark(item.process_cwd_matches)}</td><td>${mark(
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
    tr.innerHTML = `<td>${esc(costRole(row))}</td><td>${esc(row.model)}</td><td>${moneyCell(row)}</td>`;
    costBody.appendChild(tr);
  });
  document.getElementById("count-metrics").textContent = String(costs.length);
  setEmpty("metrics", "empty-metrics", costs.length);

  if (data.message) {
    document.getElementById("error").hidden = false;
    document.getElementById("error").textContent = data.message;
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
  fetch("/api/board", { headers: auth() })
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
        tr.innerHTML = `<td class='mono'>${esc(row.path)}</td><td class='mono'>${esc(
          row.branch,
        )}</td><td>${esc(changedLabel(row))}</td>`;
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

document.getElementById("run").addEventListener("click", () => {
  const auto = document.getElementById("auto").getAttribute("aria-checked") === "true";
  post("/api/runs", {
    goal: document.getElementById("goal").value,
    worker_model: document.getElementById("model").value,
    plan_approval: !auto,
  })
    .then((res) => res.json())
    .then((data) => {
      if (data.error) {
        document.getElementById("error").hidden = false;
        document.getElementById("error").textContent = data.error;
        return;
      }
      started = Date.now() / 1000;
      refresh();
    });
});

document.getElementById("new-task").addEventListener("click", () => {
  setView("kanban");
  document.getElementById("goal").focus();
});

document.querySelectorAll('.view-toggle [role="tab"]').forEach((tab) => {
  tab.addEventListener("click", () => setView(tab.dataset.view));
});

document.querySelectorAll(".mobile-lane-tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    mobileLane = btn.dataset.lane;
    applyMobileLane();
  });
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
  esc,
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
applyMobileLane();
refresh();
setInterval(refresh, 1000);
setInterval(tick, 1000);
