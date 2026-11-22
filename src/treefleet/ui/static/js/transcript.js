/**
 * Orchestrator transcript view: modes, plan approval, live /api/transcript polling.
 * Append-only DOM keyed by entry id; windowed to WINDOW_NODE_CAP nodes.
 */

import {
  ASSISTANT_JOIN,
  DEFAULT_MODE,
  INITIAL_PAGE_CAP,
  PAGE_LIMIT,
  POLL_INTERVAL_MS,
  TRANSCRIPT_MODE_KEY,
  WINDOW_NODE_CAP,
  buildTranscriptEntries,
  filterByMode,
} from "./transcript-model.js";
import { setSafeText, sanitizeBidi } from "./safe-text.js";

export {
  ASSISTANT_JOIN,
  DEFAULT_MODE,
  INITIAL_PAGE_CAP,
  PAGE_LIMIT,
  POLL_INTERVAL_MS,
  TRANSCRIPT_MODE_KEY,
  WINDOW_NODE_CAP,
};

function readMode() {
  try {
    const v = localStorage.getItem(TRANSCRIPT_MODE_KEY);
    if (v === "verbose" || v === "normal" || v === "summary") return v;
  } catch (_err) {
    /* ignore */
  }
  return DEFAULT_MODE;
}

function writeMode(mode) {
  try {
    localStorage.setItem(TRANSCRIPT_MODE_KEY, mode);
  } catch (_err) {
    /* ignore */
  }
}

function relativeTs(ts) {
  if (!ts) return "";
  const ms = Date.parse(ts);
  if (!Number.isFinite(ms)) return "";
  const delta = Math.max(0, Math.floor((Date.now() - ms) / 1000));
  if (delta < 45) return "just now";
  if (delta < 3600) return `${Math.max(1, Math.floor(delta / 60))} min ago`;
  if (delta < 86400) return `${Math.max(1, Math.floor(delta / 3600))} h ago`;
  try {
    return new Date(ms).toLocaleString();
  } catch (_err) {
    return "";
  }
}

function actorDotClass(actor) {
  const a = String(actor || "").toLowerCase();
  if (a === "you" || a === "user") return "accent";
  if (a === "worker") return "green";
  if (a === "orchestrator") return "yellow";
  return "muted";
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) setSafeText(node, text);
  return node;
}

function setSafeAttr(node, name, value) {
  node.setAttribute(name, sanitizeBidi(value == null ? "" : String(value)));
}

/**
 * @param {{ auth: Function, post: Function, onPlanApproved?: Function, selectTask?: Function }} opts
 */
export function createTranscript(opts) {
  const { auth, post, onPlanApproved } = opts;
  let mode = readMode();
  let board = {};
  let rawItems = [];
  let plan = null;
  let nextAfter = "0";
  let pollTimer = null;
  let backoffMs = POLL_INTERVAL_MS;
  let loading = false;
  let truncated = false;
  let errorMsg = "";
  let stickBottom = true;
  let unseen = 0;
  let abortPoll = false;
  let rejectConfirm = false;
  let actionBusy = false;
  let expanded = new Set();
  const nodes = new Map(); // id -> HTMLElement
  let lastTickMs = 0;

  function root() {
    return document.getElementById("orch-thread");
  }

  function scrollEl() {
    return document.querySelector(".orch-scroll") || root();
  }

  function entriesForMode() {
    const built = buildTranscriptEntries(rawItems, plan, board);
    return filterByMode(built, mode);
  }

  function windowSlice(entries) {
    if (entries.length <= WINDOW_NODE_CAP) return { slice: entries, start: 0 };
    const start = entries.length - WINDOW_NODE_CAP;
    return { slice: entries.slice(start), start };
  }

  function ensureChrome(container) {
    let header = container.querySelector(".transcript-header");
    if (!header) {
      header = el("div", "transcript-header");
      const modes = el("div", "transcript-mode");
      modes.setAttribute("role", "radiogroup");
      modes.setAttribute("aria-label", "Transcript detail");
      ["verbose", "normal", "summary"].forEach((id) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.dataset.mode = id;
        btn.setAttribute("role", "radio");
        btn.setAttribute("aria-checked", String(id === mode));
        btn.textContent = id[0].toUpperCase() + id.slice(1);
        btn.addEventListener("click", () => setMode(id));
        modes.appendChild(btn);
      });
      header.appendChild(modes);
      container.appendChild(header);
    } else {
      header.querySelectorAll("[data-mode]").forEach((btn) => {
        btn.setAttribute("aria-checked", String(btn.dataset.mode === mode));
      });
    }
    let list = container.querySelector(".transcript-list");
    if (!list) {
      list = el("div", "transcript-list");
      list.setAttribute("role", "log");
      setSafeAttr(list, "aria-label", "Orchestrator transcript");
      container.appendChild(list);
    }
    let banner = container.querySelector(".transcript-banner-slot");
    if (!banner) {
      banner = el("div", "transcript-banner-slot");
      container.insertBefore(banner, list);
    }
    let pill = container.querySelector(".transcript-new-pill");
    if (!pill) {
      pill = document.createElement("button");
      pill.type = "button";
      pill.className = "transcript-new-pill";
      pill.hidden = true;
      pill.addEventListener("click", () => {
        stickBottom = true;
        unseen = 0;
        pill.hidden = true;
        const sc = scrollEl();
        if (sc) sc.scrollTop = sc.scrollHeight;
      });
      container.appendChild(pill);
    }
    return { header, list, banner, pill };
  }

  function paintBanner(slot) {
    while (slot.firstChild) slot.removeChild(slot.firstChild);
    if (errorMsg) {
      const row = el("div", "transcript-banner is-error");
      setSafeText(row, errorMsg);
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "btn secondary";
      retry.textContent = "Retry";
      retry.addEventListener("click", () => {
        errorMsg = "";
        backoffMs = POLL_INTERVAL_MS;
        loadInitial().then(() => schedulePoll());
      });
      row.appendChild(retry);
      slot.appendChild(row);
      return;
    }
    if (truncated) {
      const row = el("div", "transcript-banner");
      setSafeText(row, "Older history truncated (20,000 event cap).");
      slot.appendChild(row);
    }
  }

  function renderToolRow(entry, parent) {
    const row = el("div", "transcript-tool");
    const verb = entry.tool || "tool";
    const label = el("span", "verb", verb);
    row.appendChild(label);
    if (entry.path) {
      const path = el("span", "path");
      setSafeText(path, entry.path);
      row.appendChild(path);
    }
    if (entry.detail) {
      const detail = el("span", "detail");
      setSafeText(detail, entry.detail);
      row.appendChild(detail);
    }
    if (entry.inProgress) {
      row.appendChild(el("span", "dur", "in progress"));
    } else if (entry.duration_ms != null) {
      row.appendChild(el("span", "dur", `${Math.round(entry.duration_ms)} ms`));
    }
    if (entry.ok === false) row.classList.add("is-fail");
    parent.appendChild(row);
  }

  function buildEntryNode(entry) {
    const wrap = el("div", `transcript-entry actor-${actorDotClass(entry.actor)}`);
    wrap.dataset.entryId = entry.id;
    wrap.dataset.kind = entry.kind;
    if (entry.actor === "you") wrap.classList.add("is-you");

    if (entry.actor !== "you") {
      const dot = el("span", `actor-dot ${actorDotClass(entry.actor)}`);
      dot.setAttribute("aria-hidden", "true");
      wrap.appendChild(dot);
    }

    const body = el("div", "transcript-body");
    const micro = el("div", "transcript-micro");
    const who = entry.actor === "you" ? "You" : entry.actor || "system";
    const when = relativeTs(entry.ts);
    setSafeText(micro, when ? `${who} · ${when}` : who);
    if (entry.ts) setSafeAttr(micro, "title", entry.ts);
    body.appendChild(micro);

    if (entry.kind === "user_goal" || entry.kind === "assistant_message") {
      const bubble = el("div", entry.kind === "user_goal" ? "transcript-bubble" : "");
      const text = el("div", "transcript-serif");
      setSafeText(text, entry.text || "");
      bubble.appendChild(text);
      body.appendChild(bubble);
    } else if (entry.kind === "plan") {
      const serif = el("div", "transcript-serif");
      setSafeText(serif, entry.rationale || "Plan");
      body.appendChild(serif);
      const ol = document.createElement("ol");
      (entry.tasks || []).forEach((task) => {
        const li = document.createElement("li");
        setSafeText(li, `${task.title || task.id || "task"} · ${(task.allowed_paths || []).join(", ")}`);
        if (task.instructions) {
          const exp = document.createElement("button");
          exp.type = "button";
          exp.className = "transcript-expand";
          exp.setAttribute("aria-expanded", String(expanded.has(`${entry.id}:${task.id}`)));
          exp.textContent = "Instructions";
          const panel = el("div", "transcript-tool-body");
          panel.hidden = !expanded.has(`${entry.id}:${task.id}`);
          setSafeText(panel, task.instructions);
          exp.addEventListener("click", () => {
            const key = `${entry.id}:${task.id}`;
            if (expanded.has(key)) expanded.delete(key);
            else expanded.add(key);
            exp.setAttribute("aria-expanded", String(expanded.has(key)));
            panel.hidden = !expanded.has(key);
          });
          li.appendChild(exp);
          li.appendChild(panel);
        }
        ol.appendChild(li);
      });
      body.appendChild(ol);
    } else if (entry.kind === "plan_approval") {
      body.appendChild(renderApprovalCard(entry));
    } else if (entry.kind === "tool_group") {
      const group = el("div", "transcript-tool-group");
      const exp = document.createElement("button");
      exp.type = "button";
      exp.className = "transcript-expand";
      const open = expanded.has(entry.id);
      exp.setAttribute("aria-expanded", String(open));
      const chev = el("span", "chev", "▾");
      exp.appendChild(chev);
      const summary = el("span", "summary");
      setSafeText(summary, entry.summary || "");
      exp.appendChild(summary);
      group.appendChild(exp);
      body.appendChild(group);
      const kids = el("div", "transcript-tool-kids");
      kids.hidden = !open;
      (entry.children || []).forEach((child) => renderToolRow(child, kids));
      body.appendChild(kids);
      exp.addEventListener("click", () => {
        if (expanded.has(entry.id)) expanded.delete(entry.id);
        else expanded.add(entry.id);
        const now = expanded.has(entry.id);
        exp.setAttribute("aria-expanded", String(now));
        kids.hidden = !now;
      });
    } else if (entry.kind === "tool_call") {
      renderToolRow(entry, body);
    } else if (entry.kind === "review_verdict") {
      const row = el("div", "transcript-sys");
      const chip = el("span", `transcript-chip ${entry.verdict || ""}`);
      setSafeText(chip, String(entry.verdict || "").replaceAll("_", " "));
      row.appendChild(chip);
      const reason = el("span", "reason");
      setSafeText(reason, entry.reason ? ` ${entry.reason}` : "");
      row.appendChild(reason);
      body.appendChild(row);
      if (entry.suggestions && entry.suggestions.length) {
        const exp = document.createElement("button");
        exp.type = "button";
        exp.className = "transcript-expand";
        exp.setAttribute("aria-expanded", String(expanded.has(entry.id)));
        exp.textContent = "Suggestions";
        const panel = el("div", "transcript-tool-body");
        panel.hidden = !expanded.has(entry.id);
        setSafeText(panel, (entry.suggestions || []).join("\n"));
        exp.addEventListener("click", () => {
          if (expanded.has(entry.id)) expanded.delete(entry.id);
          else expanded.add(entry.id);
          exp.setAttribute("aria-expanded", String(expanded.has(entry.id)));
          panel.hidden = !expanded.has(entry.id);
        });
        body.appendChild(exp);
        body.appendChild(panel);
      }
    } else if (entry.kind === "worker_exited") {
      const cls = entry.warning ? "transcript-sys is-warn" : "transcript-sys";
      const row = el("div", cls);
      setSafeText(
        row,
        `Worker ${entry.task_id || ""} ${entry.status || "exited"} · ${entry.fileCount || 0} files${
          entry.error ? ` · ${entry.error}` : ""
        }`,
      );
      body.appendChild(row);
    } else if (entry.kind === "integration") {
      const card = el("div", "transcript-bubble");
      const title = el("div", "transcript-micro", "Integration summary");
      card.appendChild(title);
      const line = el("div", "transcript-sys");
      setSafeText(
        line,
        `Merged ${(entry.merged || []).length}, excluded ${(entry.excluded || []).length}` +
          (entry.branch ? ` · ${entry.branch}` : ""),
      );
      card.appendChild(line);
      body.appendChild(card);
    } else if (entry.kind === "error" || entry.kind === "guard") {
      const cls = entry.warning || entry.kind === "error" ? "transcript-sys is-error" : "transcript-sys";
      const row = el("div", cls);
      const msg =
        entry.kind === "guard"
          ? `Guard ${entry.allowed ? "allowed" : "blocked"}${
              entry.reasons && entry.reasons.length ? `: ${entry.reasons.join("; ")}` : ""
            }`
          : `${entry.errorType || "error"}: ${entry.message || ""}`;
      setSafeText(row, msg);
      body.appendChild(row);
    } else if (entry.kind === "model_selected") {
      body.appendChild(el("div", "transcript-sys", `Model ${entry.model || ""}`));
    } else if (entry.kind === "review_started") {
      body.appendChild(el("div", "transcript-sys", `Review started${entry.round != null ? ` · round ${entry.round}` : ""}`));
    } else if (entry.kind === "reaction") {
      body.appendChild(
        el("div", "transcript-sys", `Reaction ${entry.event || ""} → ${entry.action || ""}`),
      );
    } else if (entry.kind === "pr") {
      const author = entry.body && entry.body.author ? ` ${entry.body.author}` : "";
      const taskBit = !author && entry.task_id ? ` ${entry.task_id}` : "";
      body.appendChild(el("div", "transcript-sys", `${entry.prType || "pr"}${author}${taskBit}`));
    } else if (entry.kind === "system_verbose" || entry.kind === "unknown") {
      body.appendChild(
        el(
          "div",
          "transcript-sys",
          `${entry.label || entry.sourceType || "event"}${entry.task_id ? ` · ${entry.task_id}` : ""}`,
        ),
      );
    } else {
      body.appendChild(el("div", "transcript-sys", entry.kind || "entry"));
    }

    wrap.appendChild(body);
    return wrap;
  }

  function renderApprovalCard(entry) {
    const card = el("div", "transcript-plan-card");
    card.dataset.planApproval = entry.status || "pending";
    const head = el("div", "transcript-micro");
    setSafeText(
      head,
      entry.status === "approved"
        ? `Plan approved${entry.source ? ` · ${entry.source}` : ""}`
        : entry.status === "rejected"
          ? `Plan rejected${entry.source ? ` · ${entry.source}` : ""}`
          : "Plan ready — approval needed",
    );
    card.appendChild(head);

    const steps = entry.awaiting || (plan && plan.tasks) || board.awaiting || [];
    if (steps.length) {
      const ol = document.createElement("ol");
      steps.forEach((task) => {
        const li = document.createElement("li");
        setSafeText(li, `${task.title || task.id || "task"} · ${(task.allowed_paths || []).join(", ")}`);
        ol.appendChild(li);
      });
      card.appendChild(ol);
    }

    if (board.worker_model) {
      const route =
        board.worker_model === "router"
          ? "Smart routing"
          : board.worker_model === "auto"
            ? "Cursor Auto"
            : String(board.worker_model);
      card.appendChild(el("div", "transcript-meta", `Routed: ${route}`));
    }

    if (entry.status === "pending") {
      const actions = el("div", "actions");
      if (rejectConfirm) {
        const confirm = el("div", "confirm-row");
        setSafeText(confirm, "Reject and stop this run?");
        const yes = document.createElement("button");
        yes.type = "button";
        yes.className = "btn danger";
        yes.textContent = "Reject and stop run";
        yes.disabled = actionBusy;
        yes.addEventListener("click", () => doReject());
        const no = document.createElement("button");
        no.type = "button";
        no.className = "btn ghost";
        no.textContent = "Keep reviewing";
        no.addEventListener("click", () => {
          rejectConfirm = false;
          paint();
        });
        confirm.appendChild(yes);
        confirm.appendChild(no);
        actions.appendChild(confirm);
      } else {
        const approve = document.createElement("button");
        approve.type = "button";
        approve.className = "btn primary";
        approve.textContent = actionBusy ? "Working…" : "Approve plan";
        approve.disabled = actionBusy || !board.run_id;
        approve.addEventListener("click", () => doApprove());
        const reject = document.createElement("button");
        reject.type = "button";
        reject.className = "btn ghost";
        reject.textContent = "Reject and stop run";
        reject.disabled = actionBusy || !board.run_id;
        reject.addEventListener("click", () => {
          rejectConfirm = true;
          paint();
        });
        actions.appendChild(approve);
        actions.appendChild(reject);
      }
      card.appendChild(actions);
    } else {
      const chip = el(
        "span",
        `transcript-chip ${entry.status === "approved" ? "is-success" : "is-error"}`,
      );
      setSafeText(chip, entry.status === "approved" ? "Approved" : "Rejected");
      card.appendChild(chip);
    }
    return card;
  }

  function doApprove() {
    if (!board.run_id || actionBusy) return;
    actionBusy = true;
    paint();
    if (onPlanApproved) onPlanApproved();
    post(`/api/runs/${board.run_id}/approve`)
      .catch(() => {
        errorMsg = "Approve failed";
      })
      .finally(() => {
        actionBusy = false;
        // Settled state comes from events, not the click.
        paint();
      });
  }

  function doReject() {
    if (!board.run_id || actionBusy) return;
    actionBusy = true;
    paint();
    post(`/api/runs/${board.run_id}/cancel`)
      .catch(() => {
        errorMsg = "Reject failed";
      })
      .finally(() => {
        actionBusy = false;
        rejectConfirm = false;
        paint();
      });
  }

  function paintSkeleton(list) {
    while (list.firstChild) list.removeChild(list.firstChild);
    nodes.clear();
    const sk = el("div", "transcript-skeleton");
    sk.setAttribute("data-skeleton", "1");
    for (let i = 0; i < 3; i++) sk.appendChild(el("div", "bar"));
    list.appendChild(sk);
  }

  function paint() {
    const t0 = performance.now();
    const container = root();
    if (!container) return;
    container.classList.add("transcript");
    const { list, banner, pill } = ensureChrome(container);
    paintBanner(banner);

    if (loading && !rawItems.length) {
      paintSkeleton(list);
      lastTickMs = performance.now() - t0;
      return;
    }

    if (!board.run_id && !rawItems.length) {
      while (list.firstChild) list.removeChild(list.firstChild);
      nodes.clear();
      list.appendChild(el("p", "transcript-empty", "No run yet. Describe a goal to begin."));
      lastTickMs = performance.now() - t0;
      return;
    }

    const visible = entriesForMode();
    const { slice, start } = windowSlice(visible);
    const keep = new Set(slice.map((e) => e.id));

    // Remove nodes that fell out of the window or mode
    [...nodes.entries()].forEach(([id, node]) => {
      if (!keep.has(id)) {
        node.remove();
        nodes.delete(id);
      }
    });

    // Remove skeleton / empty
    list.querySelectorAll(".transcript-skeleton, .transcript-empty, .transcript-window-note").forEach((n) => n.remove());

    if (start > 0) {
      let note = list.querySelector(".transcript-window-note");
      if (!note) {
        note = el("div", "transcript-window-note transcript-meta");
        list.insertBefore(note, list.firstChild);
      }
      setSafeText(note, `${start} earlier entries hidden for performance`);
    }

    let anchor = null;
    slice.forEach((entry) => {
      let node = nodes.get(entry.id);
      if (!node) {
        node = buildEntryNode(entry);
        nodes.set(entry.id, node);
        list.appendChild(node);
        if (!stickBottom) {
          unseen += 1;
          anchor = anchor || node;
        }
      } else if (entry.kind === "plan_approval") {
        // Refresh approval card in place (status may settle from events)
        const next = buildEntryNode(entry);
        node.replaceWith(next);
        nodes.set(entry.id, next);
      }
      // Re-order if needed without recreating
      if (node.parentNode === list && list.lastChild !== node) {
        // Only move if out of order relative to previous
      }
    });

    // Ensure DOM order matches slice
    slice.forEach((entry) => {
      const node = nodes.get(entry.id);
      if (node) list.appendChild(node);
    });

    if (stickBottom) {
      const sc = scrollEl();
      if (sc) sc.scrollTop = sc.scrollHeight;
      unseen = 0;
      pill.hidden = true;
    } else if (unseen > 0) {
      pill.hidden = false;
      pill.textContent = `${unseen} new`;
    } else {
      pill.hidden = true;
    }

    lastTickMs = performance.now() - t0;
    if (typeof window !== "undefined") {
      window.__tfTranscriptTickMs = lastTickMs;
      window.__tfTranscriptDomCount = list.querySelectorAll(".transcript-entry").length;
    }
  }

  function setMode(next) {
    if (next === mode) return;
    const sc = scrollEl();
    const list = root() && root().querySelector(".transcript-list");
    let anchorId = null;
    let anchorTop = 0;
    if (sc && list) {
      const scRect = sc.getBoundingClientRect();
      const entries = [...list.querySelectorAll(".transcript-entry")];
      for (const node of entries) {
        const r = node.getBoundingClientRect();
        if (r.bottom > scRect.top + 8) {
          anchorId = node.dataset.entryId;
          anchorTop = r.top - scRect.top;
          break;
        }
      }
    }
    mode = next;
    writeMode(mode);
    paint();
    if (anchorId && sc) {
      const node = nodes.get(anchorId);
      if (node) {
        const scRect = sc.getBoundingClientRect();
        const r = node.getBoundingClientRect();
        sc.scrollTop += r.top - scRect.top - anchorTop;
      }
    }
  }

  async function fetchPage(after, withPlan) {
    const run = board.run_id;
    if (!run) return { items: [], next: after, eof: true, reset: false, plan: null };
    const q = new URLSearchParams({
      run,
      after: String(after || "0"),
      limit: String(PAGE_LIMIT),
    });
    if (withPlan) q.set("plan", "1");
    const res = await fetch(`/api/transcript?${q}`, { headers: auth() });
    if (!res.ok) throw new Error(`transcript ${res.status}`);
    return res.json();
  }

  function seedFromBoardEvents() {
    if (rawItems.length) return;
    if (!Array.isArray(board.events) || !board.events.length) return;
    rawItems = board.events.map((event, index) => ({
      ...event,
      offset: event.offset != null ? Number(event.offset) : index,
    }));
  }

  async function loadInitial() {
    loading = true;
    truncated = false;
    const snapshot = rawItems.slice();
    rawItems = [];
    nextAfter = "0";
    plan = null;
    paint();
    if (!board.run_id) {
      loading = false;
      rawItems = snapshot;
      seedFromBoardEvents();
      paint();
      return;
    }
    try {
      let pages = 0;
      let after = "0";
      let needPlan = true;
      while (pages < INITIAL_PAGE_CAP) {
        const page = await fetchPage(after, needPlan);
        needPlan = false;
        if (page.reset) {
          rawItems = [];
          after = "0";
          pages = 0;
          continue;
        }
        if (page.plan) plan = page.plan;
        const items = page.items || [];
        rawItems = rawItems.concat(items);
        after = page.next != null ? String(page.next) : after;
        pages += 1;
        if (page.eof || !items.length) break;
        if (pages >= INITIAL_PAGE_CAP && !page.eof) truncated = true;
      }
      nextAfter = after;
      errorMsg = "";
      backoffMs = POLL_INTERVAL_MS;
    } catch (err) {
      errorMsg = "Could not load transcript";
      backoffMs = Math.min(backoffMs * 2, 8000);
      rawItems = snapshot;
    } finally {
      seedFromBoardEvents();
      loading = false;
      paint();
    }
  }

  async function pollOnce() {
    if (abortPoll || document.hidden || !board.run_id) return;
    const finished = board.stage === "done" || !!(board.report && board.report.integration);
    try {
      const needPlan = rawItems.some((i) => i.type === "awaiting_approval")
        ? false
        : !plan && !!(board.awaiting);
      // Re-request plan when a new awaiting_approval appears without plan payload
      let withPlan = false;
      if (board.awaiting && (!plan || !plan.tasks || !plan.tasks.length)) withPlan = true;
      const page = await fetchPage(nextAfter, withPlan || needPlan);
      if (page.reset) {
        await loadInitial();
        return;
      }
      if (page.plan) plan = page.plan;
      const items = page.items || [];
      if (items.length) {
        // Append-only by offset
        const seen = new Set(rawItems.map((i) => i.offset));
        for (const item of items) {
          if (item.offset != null && seen.has(item.offset)) continue;
          rawItems.push(item);
          if (item.type === "awaiting_approval") {
            // refresh plan
            try {
              const again = await fetchPage("0", true);
              if (again.plan) plan = again.plan;
            } catch (_e) {
              /* ignore */
            }
          }
        }
        nextAfter = page.next != null ? String(page.next) : nextAfter;
        paint();
      }
      errorMsg = "";
      backoffMs = POLL_INTERVAL_MS;
      if (finished && page.eof) {
        abortPoll = true;
        return;
      }
    } catch (_err) {
      errorMsg = "Transcript sync failed";
      backoffMs = Math.min(backoffMs * 2, 8000);
      paint();
    }
  }

  function schedulePoll() {
    if (pollTimer) clearTimeout(pollTimer);
    if (abortPoll) return;
    pollTimer = setTimeout(async () => {
      await pollOnce();
      schedulePoll();
    }, backoffMs);
  }

  function bindScroll() {
    const sc = scrollEl();
    if (!sc || sc.dataset.tfTranscriptBound) return;
    sc.dataset.tfTranscriptBound = "1";
    sc.addEventListener("scroll", () => {
      const near = sc.scrollHeight - sc.scrollTop - sc.clientHeight < 80;
      stickBottom = near;
      const jump = document.getElementById("jump-latest");
      if (jump) jump.hidden = near;
      if (near) {
        unseen = 0;
        const pill = root() && root().querySelector(".transcript-new-pill");
        if (pill) pill.hidden = true;
      }
    });
  }

  function renderThread(data) {
    const prevRun = board.run_id;
    board = data || {};
    bindScroll();
    if (!board.run_id) {
      abortPoll = true;
      if (pollTimer) clearTimeout(pollTimer);
      rawItems = [];
      plan = null;
      paint();
      return;
    }
    if (board.run_id !== prevRun) {
      abortPoll = false;
      rejectConfirm = false;
      stickBottom = true;
      unseen = 0;
      expanded = new Set();
      nodes.clear();
      seedFromBoardEvents();
      paint();
      loadInitial().then(() => schedulePoll());
      return;
    }
    seedFromBoardEvents();
    paint();
  }

  function bind() {
    bindScroll();
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        backoffMs = POLL_INTERVAL_MS;
        pollOnce();
      }
    });
    const jump = document.getElementById("jump-latest");
    if (jump) {
      jump.addEventListener("click", () => {
        stickBottom = true;
        unseen = 0;
        const sc = scrollEl();
        if (sc) sc.scrollTop = sc.scrollHeight;
        jump.hidden = true;
      });
    }
    document.getElementById("follow-run")?.addEventListener("click", () => {
      const auto = document.getElementById("auto")?.getAttribute("aria-checked") === "true";
      const goal = document.getElementById("follow-goal")?.value;
      const model = document.getElementById("follow-model")?.value;
      post("/api/runs", { goal, worker_model: model, plan_approval: !auto }).then((res) => res.json()).then(() => {
        stickBottom = true;
        const box = document.getElementById("follow-goal");
        if (box) box.value = "";
      });
    });
  }

  function syncModels(models) {
    const select = document.getElementById("follow-model");
    if (!select || select.options.length) return;
    models.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.id;
      setSafeText(option, item.label);
      select.appendChild(option);
    });
  }

  function resetPlanChoice() {
    rejectConfirm = false;
    actionBusy = false;
  }

  return {
    renderThread,
    bind,
    syncModels,
    resetPlanChoice,
    getMode: () => mode,
    getTickMs: () => lastTickMs,
    getDomCount: () => (root() ? root().querySelectorAll(".transcript-entry").length : 0),
    _test: {
      setRawItems(items, p) {
        rawItems = items;
        if (p) plan = p;
        paint();
      },
      paint,
      entriesForMode,
    },
  };
}
