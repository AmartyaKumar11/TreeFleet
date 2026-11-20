/** Task drawer panes. Untrusted text goes through textContent / createTextNode only. */

import { statusKey, statusWords as sharedStatusWords } from "./status.js";
import { createDiffPane } from "./diff-render.js";

const LIVE_CAP = 80;
const DIFF_CAP = 2000;

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function proofFrom(state, task) {
  if (task.proof) return task.proof;
  const rows =
    (state.report && state.report.isolation && state.report.isolation.tasks) || state.proof || [];
  const list = Array.isArray(rows) ? rows : [];
  return list.find((item) => item.task_id === task.id) || null;
}

function toolErrorText(event) {
  const payload = event.payload || {};
  if (event.type === "tool.error") return String(payload.message || payload.tool || "tool error");
  const reasons = payload.reasons || [];
  return Array.isArray(reasons) ? reasons.join("; ") : String(reasons);
}

function toolErrorsFor(boardState, task) {
  return ((boardState && boardState.events) || []).filter((event) => {
    if (event.task_id && event.task_id !== task.id) return false;
    if (event.type === "tool.error") return true;
    if (event.type !== "guard.decision") return false;
    const payload = event.payload || {};
    if (payload.stage === "tool_result") return true;
    const text = toolErrorText(event).toLowerCase();
    return /validation error|field required|error executing tool|bad_args|malformed tool result/.test(text);
  });
}

export function costRole(row) {
  const component = String((row && (row.component || row.role)) || "");
  if (component === "cursor") return "coder";
  if (component === "planner" || component === "worker" || component === "reviewer" || component === "reporter") {
    return "reasoner";
  }
  return component;
}

function fileStatSig(files) {
  if (!Array.isArray(files)) return "";
  return files.map((file) => `${file.path}:${file.additions || 0}:${file.deletions || 0}:${file.binary ? 1 : 0}`).join(",");
}

export function createDrawer({ auth, worktreeFor }) {
  let followTail = true;
  let wrapLines = false;
  let showTimestamps = false;
  let livePaused = false;
  let liveOffset = 0;
  let selected = null;
  let state = {};
  let diffSignal = "";
  let diffPayloadSig = "";
  let diffTaskId = "";
  let diffInFlight = false;
  let diffQueued = false;
  let diffToken = 0;
  let lastGood = null;
  let paneScroll = {};
  let lastTaskId = "";
  let activePaneId = "live";
  let diffRenderer = null;

  function activePane() {
    return activePaneId || "live";
  }

  function rememberPaneScroll() {
    document.querySelectorAll(".drawer .pane").forEach((pane) => {
      if (!pane.hidden) paneScroll[pane.id] = pane.scrollTop;
    });
  }

  function restorePaneScroll(id) {
    const pane = document.getElementById(id);
    if (pane && paneScroll[id] != null) pane.scrollTop = paneScroll[id];
  }

  function setPane(id) {
    const next = id || "live";
    rememberPaneScroll();
    activePaneId = next;
    document.querySelectorAll(".drawer .tab").forEach((tab) => {
      const on = tab.dataset.pane === next;
      tab.setAttribute("aria-selected", String(on));
      tab.tabIndex = on ? 0 : -1;
    });
    document.querySelectorAll(".drawer .pane").forEach((pane) => {
      pane.hidden = pane.id !== next;
    });
    restorePaneScroll(next);
    if (next === "diff" && selected) syncDiff(selected, state, { reason: "pane" });
  }

  function lineKind(raw) {
    const text = String(raw || "");
    const lower = text.toLowerCase();
    if (/error|traceback|exception|failed|fatal/.test(lower)) return "error";
    if (/^\s*(edit|write|writing|patched)\b/.test(lower) || /\bediting\b/.test(lower)) return "edit";
    if (/^\s*(run|running|\$|cmd|shell|command)\b/.test(lower)) return "command";
    if (/^\s*(read|reading|search|glob|find)\b/.test(lower)) return "search";
    return "plain";
  }

  function appendLiveLine(log, raw, ts) {
    const kind = lineKind(raw);
    const row = el("div", `live-line live-${kind}`);
    if (showTimestamps && ts) row.appendChild(el("span", "live-ts", String(ts)));
    if (kind === "edit" || kind === "command" || kind === "search") {
      const icon = kind === "edit" ? "pencil" : kind === "command" ? "terminal" : "search";
      const mark = el("span", "live-tool-icon");
      mark.innerHTML = `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#icon-${icon}"></use></svg>`;
      row.appendChild(mark);
    }
    row.appendChild(document.createTextNode(String(raw)));
    log.appendChild(row);
  }

  function renderLive(task) {
    const root = document.getElementById("live");
    let toolbar = root.querySelector(".live-toolbar");
    const events = (state.events || []).filter((event) => !event.task_id || event.task_id === task.id);
    const hasTs = events.some((event) => event.ts);
    const setFollowLabel = (follow) => {
      if (!follow) return;
      follow.setAttribute("aria-pressed", String(followTail));
      follow.classList.toggle("is-on", followTail);
      follow.innerHTML = `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#icon-arrow-down-to-line"></use></svg><span>Follow</span>`;
      follow.setAttribute("aria-label", followTail ? "Follow on" : "Follow off");
      follow.title = followTail ? "Following output" : "Follow output";
    };
    const setPauseLabel = (pause) => {
      if (!pause) return;
      pause.setAttribute("aria-pressed", String(livePaused));
      pause.classList.toggle("is-on", livePaused);
      const icon = livePaused ? "play" : "pause";
      const label = livePaused ? "Resume" : "Pause";
      pause.innerHTML = `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#icon-${icon}"></use></svg><span>${label}</span>`;
      pause.setAttribute("aria-label", label);
      pause.title = label;
    };
    if (!toolbar) {
      clear(root);
      toolbar = el("div", "live-toolbar");
      const follow = el("button", "btn ghost live-follow");
      follow.type = "button";
      setFollowLabel(follow);
      follow.addEventListener("click", () => {
        followTail = !followTail;
        if (followTail) livePaused = false;
        setFollowLabel(follow);
        setPauseLabel(toolbar.querySelector(".live-pause"));
        if (followTail) renderLive(selected || task);
        syncJump();
      });
      const wrap = el("button", "btn ghost live-wrap");
      wrap.type = "button";
      wrap.innerHTML = `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#icon-wrap-text"></use></svg><span>Wrap</span>`;
      wrap.setAttribute("aria-label", "Wrap lines");
      wrap.title = "Wrap lines";
      wrap.setAttribute("aria-pressed", "false");
      wrap.addEventListener("click", () => {
        wrapLines = !wrapLines;
        wrap.setAttribute("aria-pressed", String(wrapLines));
        wrap.classList.toggle("is-on", wrapLines);
        const log = root.querySelector(".live-log");
        if (log) log.classList.toggle("is-wrap", wrapLines);
      });
      const tsBtn = el("button", "btn ghost live-ts-toggle");
      tsBtn.type = "button";
      tsBtn.innerHTML = `<span>Timestamps</span>`;
      tsBtn.setAttribute("aria-label", "Timestamps");
      tsBtn.title = "Timestamps";
      tsBtn.hidden = !hasTs;
      tsBtn.setAttribute("aria-pressed", String(showTimestamps));
      tsBtn.addEventListener("click", () => {
        showTimestamps = !showTimestamps;
        tsBtn.setAttribute("aria-pressed", String(showTimestamps));
        tsBtn.classList.toggle("is-on", showTimestamps);
        renderLive(selected || task);
      });
      const spacer = el("span", "live-toolbar-spacer");
      const pause = el("button", "btn ghost live-pause");
      pause.type = "button";
      setPauseLabel(pause);
      pause.addEventListener("click", () => {
        livePaused = !livePaused;
        if (livePaused) followTail = false;
        setPauseLabel(pause);
        setFollowLabel(toolbar.querySelector(".live-follow"));
        syncJump();
      });
      const copy = el("button", "btn ghost icon-btn live-copy");
      copy.type = "button";
      copy.setAttribute("aria-label", "Copy");
      copy.title = "Copy";
      copy.innerHTML = `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#icon-copy"></use></svg>`;
      copy.addEventListener("click", async () => {
        const text = ((selected && selected.lines) || []).join("\n");
        try {
          if (navigator.clipboard) await navigator.clipboard.writeText(text);
          copy.title = "Copied";
          setTimeout(() => {
            copy.title = "Copy";
          }, 1200);
        } catch (_err) {
          /* ignore */
        }
      });
      toolbar.appendChild(follow);
      toolbar.appendChild(wrap);
      toolbar.appendChild(tsBtn);
      toolbar.appendChild(spacer);
      toolbar.appendChild(pause);
      toolbar.appendChild(copy);
      root.appendChild(toolbar);
      const jump = el("button", "jump-latest", "Jump to latest");
      jump.type = "button";
      jump.hidden = true;
      jump.addEventListener("click", () => {
        followTail = true;
        livePaused = false;
        setFollowLabel(toolbar.querySelector(".live-follow"));
        setPauseLabel(toolbar.querySelector(".live-pause"));
        renderLive(selected || task);
        syncJump();
      });
      root.appendChild(jump);
    } else {
      const tsBtn = toolbar.querySelector(".live-ts-toggle");
      if (tsBtn) tsBtn.hidden = !hasTs;
      setFollowLabel(toolbar.querySelector(".live-follow"));
      setPauseLabel(toolbar.querySelector(".live-pause"));
    }

    function syncJump() {
      const jump = root.querySelector(".jump-latest");
      if (jump) jump.hidden = followTail;
    }

    if (livePaused) {
      syncJump();
      return;
    }

    const lines = task.lines || [];
    const total = lines.length;
    let start = liveOffset;
    if (followTail && total > LIVE_CAP) start = Math.max(0, total - LIVE_CAP);
    const running = /run|working|editing|reading/i.test(String(task.status || task.action || "")) || task.action;
    const sig = JSON.stringify({ start, followTail, wrapLines, showTimestamps, lines });
    const existing = root.querySelector(".live-log");
    if (existing && existing.dataset.sig === sig) {
      syncJump();
      return;
    }
    root.querySelectorAll(".earlier-lines, .live-log, .live-empty").forEach((node) => node.remove());
    if (!lines.length) {
      const empty = el("div", "live-empty");
      empty.appendChild(el("h3", "type-title-lg", running ? "Waiting for output" : "No output"));
      if (running) {
        const spin = el("span", "spinner");
        spin.setAttribute("aria-hidden", "true");
        empty.appendChild(spin);
      }
      root.appendChild(empty);
      syncJump();
      return;
    }
    if (start > 0) {
      const earlier = el("button", "btn ghost earlier-lines", "Show earlier lines");
      earlier.type = "button";
      earlier.addEventListener("click", () => {
        liveOffset = Math.max(0, start - LIVE_CAP);
        followTail = false;
        renderLive(task);
      });
      root.appendChild(earlier);
    }
    const log = el("div", "live-log");
    log.classList.toggle("is-wrap", wrapLines);
    log.dataset.sig = sig;
    log.addEventListener("scroll", () => {
      if (log.scrollTop + log.clientHeight < log.scrollHeight - 24) {
        if (followTail) {
          followTail = false;
          setFollowLabel(root.querySelector(".live-follow"));
          syncJump();
        }
      }
    });
    lines.slice(start).forEach((line, i) => {
      const event = events[start + i];
      appendLiveLine(log, line, event && event.ts);
    });
    root.appendChild(log);
    if (followTail) log.scrollTop = log.scrollHeight;
    syncJump();
  }

  function initDiffRenderer() {
    const root = document.getElementById("diff");
    if (!root || diffRenderer) return;
    diffRenderer = createDiffPane(root, {
      auth,
      getWorktreePath: worktreeFor,
      onFetch: (opts) => {
        if (selected) {
          fetchDiff(selected, opts);
        }
      }
    });
  }

  function diffChangeSignal(task, boardState) {
    const board = boardState || state || {};
    const branch = String(task.branch || "");
    const row = (board.worktrees || []).find((item) => String(item.branch || "") === branch);
    const head = row ? String(row.head || "") : "";
    const changed = row ? String(row.changed ?? "") : "";
    const wtFiles = fileStatSig(row && row.files);
    const taskFiles = fileStatSig(task.files);
    const status = String(task.status || "");
    const embedded =
      task.diff != null || task.diff_files
        ? `emb:${String(task.diff || "").length}:${fileStatSig(task.diff_files)}`
        : "";
    return [head, changed, wtFiles, taskFiles, status, embedded].join("|");
  }

  function payloadSig(data) {
    return JSON.stringify({
      diff: data && data.diff != null ? String(data.diff) : "",
      files: (data && (data.files || data.diff_files)) || [],
      error: data && data.error != null ? String(data.error) : "",
    });
  }

  function paintDiffData(task, data, opts) {
    initDiffRenderer();
    if (!diffRenderer) return;
    const next = payloadSig(data);
    if (opts && opts.skipUnchanged && next === diffPayloadSig && diffTaskId === task.id) {
      return;
    }
    const preserve = !!(opts && opts.preserve);
    diffRenderer.render(data, { preserve, skeleton: false });
    diffPayloadSig = next;
    diffTaskId = task.id;
    lastGood = { taskId: task.id, data };
  }

  function showDiffFailure(task, message) {
    initDiffRenderer();
    if (!diffRenderer) return;
    if (lastGood && lastGood.taskId === task.id) {
      paintDiffData(task, lastGood.data, { preserve: true, skipUnchanged: false });
      diffPayloadSig = payloadSig(lastGood.data);
      const root = document.getElementById("diff");
      if (root) {
        let note = root.querySelector(".diff-refresh-note");
        if (!note) {
          note = el("p", "diff-refresh-note empty-line", message || "could not refresh");
          root.insertBefore(note, root.firstChild);
        } else {
          note.textContent = message || "could not refresh";
        }
      }
      return;
    }
    diffRenderer.render({ error: message || "could not refresh" }, { preserve: false });
  }

  function finishDiffFetch(task) {
    diffInFlight = false;
    if (diffQueued && selected && selected.id === task.id && activePane() === "diff") {
      diffQueued = false;
      syncDiff(selected, state, { reason: "queued" });
      return;
    }
    diffQueued = false;
  }

  function fetchDiff(task, opts = {}) {
    const path = worktreeFor(task);
    initDiffRenderer();
    if (!path) {
      if (!(lastGood && lastGood.taskId === task.id)) {
        if (diffRenderer) {
          diffRenderer.render(null, { skeleton: false });
        }
      }
      return;
    }
    if (diffInFlight) {
      diffQueued = true;
      return;
    }
    diffInFlight = true;
    const token = ++diffToken;
    const taskId = task.id;
    if (!(lastGood && lastGood.taskId === taskId) && diffRenderer) {
      diffRenderer.render(null, { skeleton: true });
    }
    
    // Build URL with optional parameters
    let url = `/api/diff?path=${encodeURIComponent(path)}`;
    if (opts.full) url += `&full=1`;
    if (opts.file) url += `&file=${encodeURIComponent(opts.file)}`;
    
    fetch(url, { headers: auth() })
      .then((res) => {
        if (!res.ok) throw new Error(`diff ${res.status}`);
        return res.json();
      })
      .then((data) => {
        if (token !== diffToken || !selected || selected.id !== taskId || activePane() !== "diff") return;
        paintDiffData(task, data, { preserve: !!(lastGood && lastGood.taskId === taskId), skipUnchanged: true });
      })
      .catch(() => {
        if (token !== diffToken || !selected || selected.id !== taskId) return;
        showDiffFailure(task, "could not refresh");
      })
      .finally(() => finishDiffFetch(task));
  }

  function syncDiff(task, boardState, opts) {
    if (!task || activePane() !== "diff") return;
    state = boardState || state;
    const signal = diffChangeSignal(task, state);
    const taskChanged = diffTaskId !== "" && diffTaskId !== task.id;
    if (taskChanged) {
      diffPayloadSig = "";
      lastGood = null;
      diffSignal = "";
      diffToken += 1;
      diffInFlight = false;
      diffQueued = false;
    }
    const reason = (opts && opts.reason) || "refresh";
    const mustLoad = reason === "show" || (reason === "pane" && !(lastGood && lastGood.taskId === task.id));
    // Signal includes worktree head, working-tree file stats, and status (so done/failed/cancelled re-fetches).
    if (!mustLoad && signal === diffSignal) return;
    diffSignal = signal;
    if (task.diff != null || task.diff_files) {
      paintDiffData(
        task,
        { diff: task.diff || "", files: task.diff_files || [] },
        { preserve: !taskChanged && !!lastGood, skipUnchanged: !taskChanged },
      );
      return;
    }
    fetchDiff(task);
  }

  function renderDiff(task) {
    diffSignal = "";
    diffPayloadSig = "";
    lastGood = null;
    diffToken += 1;
    diffInFlight = false;
    diffQueued = false;
    syncDiff(task, state, { reason: "show" });
  }

  function statusWords(task) {
    return sharedStatusWords(task);
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

  function paintToolErrors(list, boardState, task) {
    const errors = toolErrorsFor(boardState, task);
    detailRow(list, {
      ok: errors.length === 0,
      label: "Tool errors",
      value: String(errors.length),
      detail: errors.length ? errors.map((event) => toolErrorText(event)).join("\n") : "",
      open: errors.length > 0,
    });
  }

  function detailRow(host, { ok, label, value, detail, open }) {
    const row = el("div", `detail-row${ok ? "" : " is-fail"}`);
    const btn = el("button", "detail-row-head");
    btn.type = "button";
    const icon = el("span", "detail-status");
    icon.innerHTML = `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#icon-${
      ok ? "check" : "alert-circle"
    }"></use></svg>`;
    btn.appendChild(icon);
    const sr = el("span", "visually-hidden", ok ? "pass" : "fail");
    btn.appendChild(sr);
    const labelEl = el("span", "detail-label type-body-sm", label);
    labelEl.title = String(label || "");
    btn.appendChild(labelEl);
    const valueEl = el("span", `detail-value type-code-sm${ok ? "" : " is-fail"}`, value);
    btn.appendChild(valueEl);
    const chev = el("span", "detail-chevron");
    chev.innerHTML = `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#icon-chevron-down"></use></svg>`;
    btn.appendChild(chev);
    const body = el("pre", "detail-body check-output type-code-sm");
    body.textContent = detail || "";
    if (!detail) {
      chev.hidden = true;
      btn.disabled = true;
      body.hidden = true;
    } else {
      body.hidden = !open;
      btn.setAttribute("aria-expanded", String(!!open));
      row.classList.toggle("is-open", !!open);
      btn.addEventListener("click", () => {
        const next = body.hidden;
        body.hidden = !next;
        row.classList.toggle("is-open", next);
        btn.setAttribute("aria-expanded", String(next));
      });
    }
    row.appendChild(btn);
    if (detail) row.appendChild(body);
    host.appendChild(row);
    return row;
  }

  function renderProof(task) {
    const root = document.getElementById("proofpane");
    clear(root);
    const proof = proofFrom(state, task);
    const list = el("div", "detail-list");
    if (!proof) {
      root.appendChild(el("p", "empty-line type-body-sm", "No isolation proof for this task yet."));
      paintToolErrors(list, state, task);
      root.appendChild(list);
      return;
    }
    const rows = [
      ["Process cwd", proof.process_cwd_matches, proof.process_cwd_matches ? "yes" : "no"],
      ["Toplevel", proof.toplevel_matches, proof.toplevel_matches ? "yes" : "no"],
      ["Registered", proof.registered_in_worktree_list, proof.registered_in_worktree_list ? "yes" : "no"],
      ["Canary absent", proof.canary_absent, proof.canary_absent ? "yes" : "no"],
      [
        "Within scope",
        proof.changes_within_allowed_paths,
        proof.changes_within_allowed_paths ? "yes" : "no",
      ],
    ];
    rows.forEach(([label, ok, value]) => {
      let detail = "";
      if (label === "Process cwd" && !ok && proof.cwd_attempts && proof.cwd_attempts.length) {
        detail = proof.cwd_attempts
          .map((attempt) => {
            const children = (attempt.child_pids || []).join(",");
            const head = `pid ${attempt.pid ?? "unresolved"} started ${attempt.pid_started ?? "-"} alive ${attempt.alive} children ${children}${attempt.error ? " " + attempt.error : ""}`;
            const lines = (attempt.processes || []).map((proc) =>
              proc.error ? `  ${proc.pid} ${proc.error}` : `  ${proc.pid} ${proc.cwd}`,
            );
            return [head, ...lines].join("\n");
          })
          .join("\n");
      }
      detailRow(list, { ok: !!ok, label, value, detail, open: !ok });
    });
    paintToolErrors(list, state, task);
    root.appendChild(list);
    if (proof.contained_violation || (proof.reverted_paths && proof.reverted_paths.length)) {
      root.appendChild(el("p", "violation-title", "Contained violation · reverted paths"));
      const paths = el("ul", "reverted-paths");
      (proof.reverted_paths || []).forEach((path) => {
        const item = document.createElement("li");
        item.textContent = String(path);
        paths.appendChild(item);
      });
      root.appendChild(paths);
    }
  }

  function renderChecks(task) {
    const root = document.getElementById("checks");
    clear(root);
    const checks = task.checks || [];
    if (!checks.length) {
      root.appendChild(el("p", "empty-line type-body-sm", "No checks ran for this task."));
    } else {
      const groups = new Map();
      checks.forEach((check) => {
        const source = String(check.source || check.group || "checks");
        if (!groups.has(source)) groups.set(source, []);
        groups.get(source).push(check);
      });
      groups.forEach((items, source) => {
        const card = el("div", "check-group");
        card.appendChild(el("p", "type-micro detail-group", source));
        const list = el("div", "detail-list");
        items.forEach((check) => {
          const ok = check.passed !== false;
          const value = `exit ${check.exit_code ?? "?"} · ${
            check.duration_s != null ? `${check.duration_s}s` : "—"
          }`;
          const row = detailRow(list, {
            ok,
            label: check.name || "check",
            value,
            detail: check.output ? String(check.output) : "",
            open: !ok,
          });
          if (row) row.classList.add("check-row");
        });
        card.appendChild(list);
        root.appendChild(card);
      });
    }
    const reason = String(task.reason || "").trim();
    const suggestions = Array.isArray(task.suggestions) ? task.suggestions.filter(Boolean) : [];
    if (reason || suggestions.length) {
      root.appendChild(el("p", "violation-title", "Review"));
      if (reason) root.appendChild(el("p", "review-text", reason));
      suggestions.forEach((item) => root.appendChild(el("p", "review-text", String(item))));
    }
    const detail = [];
    if (task.cursor_message) detail.push(String(task.cursor_message));
    if (task.error) detail.push(String(task.error));
    if (task.status === "needs_review") {
      detail.push(`git -C ${state.repo || ""} merge ${task.branch || task.id}`);
    }
    if (detail.length) {
      root.appendChild(el("p", "violation-title", "Detail"));
      const block = el("pre", "check-output");
      block.hidden = false;
      block.textContent = detail.join("\n");
      root.appendChild(block);
    }
  }

  function formatUsd(value, label) {
    const tag = String(label || "");
    if (/usage not reported/i.test(tag)) return "usage not reported";
    if (/unpriced|rate unknown/i.test(tag)) return "unpriced";
    if (value == null || value === "" || Number.isNaN(Number(value))) return "unpriced";
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return "unpriced";
    if (n < 0.0001) return "<$0.0001";
    if (n < 1) return `$${n.toFixed(4)}`;
    return `$${n.toFixed(2)}`;
  }

  function formatTokens(n) {
    if (n == null || n === "") return "—";
    const num = Number(n);
    if (!Number.isFinite(num)) return String(n);
    return num.toLocaleString("en-US");
  }

  function renderCost(task) {
    const root = document.getElementById("costpane");
    clear(root);
    const costs =
      (task.costs && task.costs.length && task.costs) ||
      (state.report && state.report.metrics && state.report.metrics.costs) ||
      [];
    if (!costs.length) {
      root.appendChild(el("p", "empty-line type-body-sm", "No cost rows yet."));
      return;
    }
    const grid = el("div", "cost-table");
    const head = el("div", "cost-row cost-head type-micro");
    ["Role", "Tokens", "USD", "Share"].forEach((label, i) => {
      const cell = el("span", i === 0 ? "cost-role" : "cost-num", label);
      head.appendChild(cell);
    });
    grid.appendChild(head);
    let total = 0;
    let totalTokens = 0;
    let priced = false;
    const pricedRows = costs
      .map((row) => Number(row.usd))
      .filter((n) => Number.isFinite(n) && n > 0);
    const maxShare = pricedRows.reduce((a, b) => a + b, 0) || 1;
    costs.forEach((row) => {
      const tr = el("div", "cost-row");
      tr.appendChild(el("span", "cost-role", costRole(row)));
      tr.appendChild(el("span", "cost-num mono tabular", formatTokens(row.tokens)));
      const usd = el("span", "cost-num mono tabular");
      const text = formatUsd(row.usd, row.label);
      if (text === "unpriced" || text === "usage not reported") {
        usd.appendChild(el("span", "pill", text));
      } else {
        usd.textContent = text;
        if (row.usd != null && !Number.isNaN(Number(row.usd))) {
          total += Number(row.usd);
          priced = true;
        }
      }
      tr.appendChild(usd);
      const barCell = el("span", "cost-bar-cell");
      const track = el("span", "cost-bar-track");
      const bar = el("span", "cost-bar");
      const share = Number(row.usd);
      let pct = 0;
      if (Number.isFinite(share) && share > 0) {
        pct = Math.max(3, Math.round((share / maxShare) * 100));
      }
      bar.style.width = pct ? `${pct}%` : "0";
      track.appendChild(bar);
      barCell.appendChild(track);
      tr.appendChild(barCell);
      const tok = Number(row.tokens);
      if (Number.isFinite(tok)) totalTokens += tok;
      grid.appendChild(tr);
    });
    const fr = el("div", "cost-row cost-total-row");
    fr.appendChild(el("span", "cost-role", "Total"));
    fr.appendChild(el("span", "cost-num mono tabular", formatTokens(totalTokens)));
    const totalTd = el("span", "cost-num mono tabular cost-total");
    totalTd.textContent = priced ? formatUsd(total) : "unpriced";
    fr.appendChild(totalTd);
    fr.appendChild(el("span", "cost-bar-cell", ""));
    grid.appendChild(fr);
    root.appendChild(grid);
  }

  function paintHeader(task) {
    const title = document.getElementById("task-title");
    const meta = document.getElementById("task-meta");
    const badge = document.getElementById("task-status-badge");
    const copy = document.getElementById("branch-copy");
    if (title) {
      title.textContent = task.title || task.id || "Task";
      title.title = task.title || "";
    }
    const branch = task.branch || "";
    if (meta) {
      meta.textContent = middleTruncate(branch);
      meta.title = branch;
      meta.setAttribute("data-branch", branch);
    }
    if (copy) copy.hidden = !branch;
    if (badge) {
      const words = statusWords(task);
      const key = statusKey(task);
      badge.hidden = !words;
      badge.textContent = words;
      badge.dataset.status = key;
      // Never use the absolute-positioned `.badge` notification class here.
      badge.className = `badge-status drawer-status status-${key}`;
    }
  }

  function show(task, boardState, opts) {
    const keepTab = !!(opts && opts.keepTab);
    const fade = !!(opts && opts.fade);
    const swapped = String(task.id) !== String(lastTaskId);
    selected = task;
    state = boardState || {};
    if (swapped) {
      paneScroll = {};
      lastTaskId = String(task.id);
      if (!keepTab) activePaneId = "live";
    }
    if (!(opts && opts.keepOffset)) liveOffset = 0;
    if (!(opts && opts.keepFollow)) followTail = true;
    const empty = document.getElementById("drawer-empty");
    const taskPane = document.getElementById("drawer-task");
    if (empty) empty.hidden = true;
    if (taskPane) taskPane.hidden = false;
    paintHeader(task);
    const host = document.getElementById("task-drawer") || document.querySelector(".drawer");
    if (fade && host) {
      host.classList.add("is-fading");
      setTimeout(() => host.classList.remove("is-fading"), 100);
    }
    renderLive(task);
    renderDiff(task);
    renderProof(task);
    renderChecks(task);
    renderCost(task);
    // Single source of truth: tab selection and visible panel always re-applied together.
    setPane(activePaneId || "live");
  }

  function refresh(task, boardState) {
    if (!task) return;
    selected = task;
    state = boardState || state;
    const pane = activePane();
    if (pane === "live") renderLive(task);
    if (pane === "diff") syncDiff(task, state, { reason: "refresh" });
    if (pane === "proofpane") renderProof(task);
    if (pane === "checks") renderChecks(task);
    if (pane === "costpane") renderCost(task);
  }

  function bind() {
    document.querySelectorAll(".drawer .tab").forEach((tab) => {
      tab.addEventListener("click", () => setPane(tab.dataset.pane));
    });
    const list = document.querySelector(".drawer .tabs");
    if (list) {
      list.addEventListener("keydown", (event) => {
        const tabs = [...list.querySelectorAll("[role='tab']:not([disabled])")];
        const index = tabs.indexOf(document.activeElement);
        if (index < 0) return;
        if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
          event.preventDefault();
          const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : (index - 1 + tabs.length) % tabs.length;
          tabs[next].focus();
          tabs[next].click();
        }
      });
    }
  }

  return { show, refresh, bind, setPane };
}
