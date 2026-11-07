const token = document.querySelector('meta[name="tf-token"]').content;
const replay = document.documentElement.dataset.replay === "true";
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

function notifyHuman(needs) {
  const badge = document.getElementById("attention-badge");
  const legacy = document.getElementById("attention");
  if (needs) {
    if (badge) badge.hidden = false;
    if (legacy) legacy.hidden = false;
    document.title = `(1) ${baseTitle}`;
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
  lastNeedsHuman = !!needs;
}

function filesFor(task) {
  const rows = state.worktrees || [];
  for (const row of rows) {
    const branch = String(row.branch || "");
    const path = String(row.path || "");
    if (
      (task.branch && branch === task.branch) ||
      (task.id && (path.includes(task.id) || branch.includes(task.id)))
    ) {
      if (row.files && row.files.length) return row.files;
    }
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
  const branch = task.branch || "";
  for (const row of state.worktrees || []) {
    if (row.branch === branch || (branch && String(row.path).includes(task.id))) return row.path;
  }
  return "";
}

function moneyCell(row) {
  if (row.label === "usage not reported") return "<span class='pill'>usage not reported</span>";
  if (!row.usd) return "<span class='pill'>unpriced</span>";
  return esc(String(row.usd));
}

function proofTable(proof) {
  const rows = [
    ["Process cwd", proof.process_cwd_matches],
    ["Toplevel", proof.toplevel_matches],
    ["Registered", proof.registered_in_worktree_list],
    ["Canary absent", proof.canary_absent],
    ["Within scope", proof.changes_within_allowed_paths],
  ];
  return `<table><tbody>${rows
    .map((row) => `<tr><th>${esc(row[0])}</th><td>${row[1] ? "yes" : "NO"}</td></tr>`)
    .join("")}</tbody></table>`;
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
}

function renderCard(task) {
  const button = document.createElement("button");
  button.className = "card";
  button.type = "button";
  button.dataset.taskId = task.id;
  const files = filesFor(task);
  const fileLine = files
    .map((file) => {
      const name = typeof file === "string" ? file : file.path;
      const plus = typeof file === "string" ? "" : ` +${file.additions || 0} -${file.deletions || 0}`;
      return esc(name) + plus;
    })
    .join(", ");
  const counts = lineCounts(files);
  const label = task.status === "needs_review" ? "needs_review" : task.verdict || task.status || "working";
  const merge =
    task.status === "needs_review"
      ? `git -C ${state.repo || ""} merge ${task.branch || task.id}`
      : "";
  const detail = [task.reason, task.cursor_message, task.suggestions, merge, task.error]
    .filter(Boolean)
    .join(" · ");
  const action = task.action || "";
  const spinning = lane(task) === "working" && action;
  const chips = [];
  if (task.checks) {
    task.checks.forEach((check) => {
      const name = typeof check === "string" ? check : check.name || "check";
      const passed = typeof check === "string" ? true : check.passed !== false;
      chips.push(`<span class="chip ${passed ? "pass" : "fail"}">${esc(name)}${passed ? " pass" : " fail"}</span>`);
    });
  }
  if (task.verdict || task.status === "needs_review") {
    chips.push(`<span class="chip verdict">${esc(label)}</span>`);
  } else if (task.status) {
    chips.push(`<span class="chip">${esc(label)}</span>`);
  }
  if (task.round) chips.push(`<span class="chip round">revise ${esc(task.round)}</span>`);
  if (counts.count) {
    chips.push(`<span class="chip lines">+${counts.added} −${counts.removed}</span>`);
  }
  const detailBlock = detail
    ? `<div class="${lane(task) === "needs" ? "reason" : "mono"}" style="margin-top:8px${
        lane(task) === "needs" ? "" : ";color:var(--muted)"
      }">${esc(detail)}</div>`
    : "";
  const mergeBtn =
    lane(task) === "ready"
      ? `<div class="merge-row"><button class="btn" type="button" data-merge="${esc(
          `git -C ${state.repo || ""} merge ${task.branch || task.id}`,
        )}">Copy merge command</button></div>`
      : "";
  button.innerHTML = `
    <div class="model-row">${modelIcon()}<span>${esc(task.model || "model")}</span></div>
    <div class="title">${esc(task.title)}</div>
    <span class="branch mono">${gitGlyph()}${esc(task.branch || task.id)}</span>
    <div class="status">${esc(label)}${task.round ? ` · round ${esc(task.round)}` : ""}</div>
    ${
      action
        ? `<div class="action-line mono">${spinning ? '<span class="spinner" aria-hidden="true"></span>' : ""}${esc(action)}</div>`
        : ""
    }
    <div class="chips">${chips.join("")}</div>
    <div class="mono" style="margin-top:8px;color:var(--muted)">${fileLine}</div>
    ${detailBlock}
    <div class="mono">${esc(task.model || "")}</div>
    ${mergeBtn}
  `;
  button.addEventListener("click", (event) => {
    if (event.target.closest("[data-merge]")) {
      event.stopPropagation();
      const cmd = event.target.getAttribute("data-merge");
      if (cmd && navigator.clipboard) navigator.clipboard.writeText(cmd);
      return;
    }
    select(task, true);
  });
  return button;
}

function drawMap(rows) {
  const svg = document.getElementById("map");
  if (!svg) return;
  const height = Math.max(96, 48 + rows.length * 44);
  svg.setAttribute("viewBox", `0 0 900 ${height}`);
  const paths = rows
    .map((row, index) => {
      const y = 52 + index * 44;
      let label = `${row.branch || row.path || ""}  ${changedLabel(row)}`;
      if (label.length > 64) label = `${label.slice(0, 61)}...`;
      return (
        `<path class="draw" d="M40 20 C40 ${y} 80 ${y} 120 ${y} L860 ${y}" fill="none" stroke="#4c8dff" stroke-width="2" pathLength="1"/>` +
        `<text x="130" y="${y - 10}" fill="#c6c7cc" font-size="13">${esc(label)}</text>`
      );
    })
    .join("");
  svg.innerHTML =
    `<line x1="24" y1="20" x2="876" y2="20" stroke="rgba(255,255,255,.18)" stroke-width="2"/>` +
    `<text x="24" y="16" fill="#c6c7cc" font-size="13">main</text>${paths}`;
}

function select(task, fromUser) {
  if (fromUser) pinned = true;
  if (selected && selected.id === task.id && !fromUser) {
    document.getElementById("live").textContent = (task.lines || []).join("\n");
    return;
  }
  selected = task;
  document.getElementById("task-title").textContent = task.title;
  document.getElementById("task-meta").textContent = `${task.model || ""} ${task.branch || ""}`;
  document.getElementById("live").textContent = (task.lines || []).join("\n");
  const proof = (
    (state.report && state.report.isolation && state.report.isolation.tasks) ||
    state.proof ||
    []
  ).filter((item) => item.task_id === task.id)[0];
  document.getElementById("proofpane").innerHTML = proof
    ? proofTable(proof)
    : "Proof rows are in the isolation table.";
  const path = worktreeFor(task);
  document.getElementById("diff").textContent = path ? "Loading diff…" : "No worktree for this task yet.";
  if (!path) return;
  fetch(`/api/diff?path=${encodeURIComponent(path)}`, { headers: auth() })
    .then((res) => res.json())
    .then((data) => {
      const files = (data.files || [])
        .map((file) => `${file.path} +${file.additions || 0} -${file.deletions || 0}`)
        .join("\n");
      document.getElementById("diff").textContent =
        `${files ? `${files}\n\n` : ""}${data.diff || data.error || "No diff."}`;
    });
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
        reader.read().then((chunk) => {
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
        }).catch(() => {});
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
  document.getElementById("clean").textContent = data.clean
    ? "Main checkout clean"
    : "Main checkout dirty";
  document.getElementById("stage").textContent = `Stage: ${data.stage || "guard"}`;
  notifyHuman(!!data.needs_human || !!data.awaiting);
  if (data.started_at) started = data.started_at;
  if (data.finished_at) finished = data.finished_at;
  else if (data.stage === "done" && !finished) finished = Date.now() / 1000;

  const runList = document.getElementById("run-history");
  if (runList) {
    runList.innerHTML = "";
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("aria-current", "true");
    const statusDot =
      data.needs_human || data.awaiting
        ? "orange"
        : data.stage === "done"
          ? "green"
          : "blue";
    btn.innerHTML = `<span class="dot ${statusDot}"></span><span class="mono">${esc(
      data.run_id || "idle",
    )}</span>`;
    li.appendChild(btn);
    runList.appendChild(li);
  }

  ["needs", "working", "review", "ready"].forEach((id) => {
    document.getElementById(id).innerHTML = "";
  });

  if (data.awaiting) {
    const card = document.createElement("div");
    card.className = "card plan";
    const items = data.awaiting
      .map((task) => `<li>${esc(task.title)} · ${esc((task.allowed_paths || []).join(", "))}</li>`)
      .join("");
    card.innerHTML = `<div class="title">Plan ready: ${data.awaiting.length} tasks</div><ul>${items}</ul>`;
    const actions = document.createElement("div");
    actions.className = "actions";
    const ok = document.createElement("button");
    ok.className = "btn go";
    ok.type = "button";
    ok.textContent = "Approve plan";
    ok.addEventListener("click", () => post(`/api/runs/${data.run_id}/approve`));
    const cancel = document.createElement("button");
    cancel.className = "btn";
    cancel.type = "button";
    cancel.textContent = "Cancel";
    cancel.addEventListener("click", () => post(`/api/runs/${data.run_id}/cancel`));
    actions.appendChild(ok);
    actions.appendChild(cancel);
    card.appendChild(actions);
    document.getElementById("needs").appendChild(card);
  }

  const tasks = data.tasks || [];
  setLaneCounts(tasks, !!data.awaiting);
  tasks.forEach((task) => {
    document.getElementById(lane(task)).appendChild(renderCard(task));
  });
  playFlip();
  applyMobileLane();

  if (!pinned) {
    let current = null;
    tasks.forEach((task) => {
      if (!current && lane(task) === "working") current = task;
    });
    if (current) select(current, false);
  } else if (selected) {
    const held = tasks.find((task) => task.id === selected.id);
    if (held) select(held, false);
  }

  const body = document.querySelector("#worktrees tbody");
  body.innerHTML = "";
  (data.worktrees || []).forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class='mono'>${esc(row.path)}</td><td class='mono'>${esc(
      row.branch,
    )}</td><td>${esc(changedLabel(row))}</td>`;
    body.appendChild(tr);
  });
  drawMap(data.worktrees || []);
  if (data.run_id) openEvents(data.run_id);

  const guards = document.getElementById("guards");
  guards.innerHTML = "";
  (data.events || [])
    .filter((event) => event.type === "guard.decision")
    .forEach((event) => {
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

  const integration = document.getElementById("integration");
  const merged = data.report && data.report.integration;
  integration.textContent = merged
    ? `Branch ${merged.branch || ""} checks ${(merged.checks || [])
        .map((c) => `${c.name}${c.passed ? " pass" : " fail"}`)
        .join(", ") || "none"}`
    : "No merge yet.";

  const costBody = document.querySelector("#metrics tbody");
  costBody.innerHTML = "";
  const costs = (data.report && data.report.metrics && data.report.metrics.costs) || [];
  costs.forEach((row) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${esc(row.component)}</td><td>${esc(row.model)}</td><td>${moneyCell(row)}</td>`;
    costBody.appendChild(tr);
  });

  if (data.message) {
    document.getElementById("error").hidden = false;
    document.getElementById("error").textContent = data.message;
  }
}

function tick() {
  if (!started) return;
  const end = finished || Date.now() / 1000;
  const shown = clock(end - started);
  document.getElementById("elapsed").textContent = shown;
  document.getElementById("metrics-elapsed").textContent = `Elapsed ${shown}`;
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
      drawMap(state.worktrees);
    })
    .catch(() => {});
}

function setView(next) {
  view = next;
  document.getElementById("view-kanban").hidden = next !== "kanban";
  document.getElementById("view-orchestrator").hidden = next !== "orchestrator";
  document.querySelectorAll('.view-toggle [role="tab"]').forEach((tab) => {
    tab.setAttribute("aria-selected", String(tab.dataset.view === next));
  });
}

document.getElementById("auto").addEventListener("click", () => {
  const button = document.getElementById("auto");
  button.setAttribute("aria-checked", String(button.getAttribute("aria-checked") !== "true"));
});

document.querySelectorAll(".drawer .tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".drawer .tab").forEach((item) => {
      item.setAttribute("aria-selected", String(item === tab));
    });
    document.querySelectorAll(".drawer .pane").forEach((pane) => {
      pane.hidden = pane.id !== tab.dataset.pane;
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
  });

if (replay) document.getElementById("replay").hidden = false;
applyMobileLane();
refresh();
setInterval(refresh, 1000);
setInterval(tick, 1000);
