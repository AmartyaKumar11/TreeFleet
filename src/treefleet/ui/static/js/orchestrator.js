/** Orchestrator thread built from the board snapshot and SSE events. */

export function createOrchestrator({ esc, clock, post, selectTask, auth, onPlanApproved }) {
  let planChoice = null;
  let stickToBottom = true;
  let startingIds = null;

  function routingLabel(model) {
    if (!model || model === "router") return "Smart routing";
    if (model === "auto") return "Cursor Auto";
    return model;
  }

  function guardEvents(data) {
    return (data.events || []).filter((event) => event.type === "guard.decision");
  }

  function inputGuard(data) {
    const rows = guardEvents(data).filter((event) => (event.payload || {}).stage === "input");
    return rows.length ? rows[rows.length - 1] : null;
  }

  function exploredFiles(data) {
    if (Array.isArray(data.explored) && data.explored.length) return data.explored;
    for (const event of data.events || []) {
      const payload = event.payload || {};
      if (event.type === "repo_scan" && Array.isArray(payload.files)) return payload.files;
      if (event.type === "phase" && payload.name === "scan") {
        if (Array.isArray(payload.files)) return payload.files;
        if (payload.file_count) {
          return Array.from({ length: Number(payload.file_count) }, (_, i) => `file-${i + 1}`);
        }
      }
    }
    return [];
  }

  function conflictLabel(task) {
    if (task.conflict_state) return task.conflict_state;
    const reason = `${task.reason || ""} ${task.error || ""}`.toLowerCase();
    if (task.status === "conflict" || /conflict/.test(reason) || task.conflict) {
      if (task.conflict_resolved) return "conflict resolved";
      if (task.conflict_resolving) return "conflict, resolving";
      return "conflict, excluded";
    }
    return "";
  }

  function workerRows(data) {
    if (startingIds && startingIds.length) {
      return startingIds.map((task) => ({
        id: task.id,
        title: task.title,
        branch: task.branch || `tf/${data.run_id || "run"}/${task.id}`,
        model: routingLabel(task.model || data.worker_model || "router"),
        state: "starting",
        spinning: true,
        action: "",
      }));
    }
    if (data.awaiting && data.awaiting.length) {
      return data.awaiting.map((task) => ({
        id: task.id,
        title: task.title,
        branch: `tf/${data.run_id || "run"}/${task.id}`,
        model: routingLabel(data.worker_model || "router"),
        state: "queued",
        spinning: false,
        action: "",
      }));
    }
    return (data.tasks || []).map((task) => {
      const running = task.lane === "working" && !!task.action;
      let state = conflictLabel(task) || task.verdict || task.status || task.lane || "pending";
      if (running) state = "running";
      return {
        id: task.id,
        title: task.title,
        branch: task.branch || task.id,
        model: routingLabel(task.model || data.worker_model || "model"),
        state,
        spinning: running || state === "starting" || state === "conflict, resolving",
        action: task.action || "",
      };
    });
  }

  function tooltipFor(guard) {
    const payload = (guard && guard.payload) || {};
    const reasons = payload.reasons || [];
    const scores = payload.scores || {};
    const scoreLines = Object.keys(scores).map((key) => `${key}: ${scores[key]}`);
    return [...reasons, ...scoreLines].join("\n") || "No score details";
  }

  function verdictDot(label) {
    if (/conflict|reject|fail|excluded/.test(label)) return "red";
    if (/needs_review|revise|resolving/.test(label)) return "orange";
    if (/approve|resolved|ready/.test(label)) return "green";
    return "yellow";
  }

  function renderThread(data) {
    const root = document.getElementById("orch-thread");
    if (!root) return;
    const nearBottom = root.scrollHeight - root.scrollTop - root.clientHeight < 80;
    const goal = data.goal || (data.report && data.report.goal) || "";
    const model = routingLabel(data.worker_model || "router");
    const guard = inputGuard(data);
    const explored = exploredFiles(data);
    const workers = workerRows(data);
    const parts = [];

    if (goal) {
      parts.push(
        `<div class="bubble-row right"><div class="bubble goal"><div class="bubble-meta">${esc(
          model,
        )}</div><div>${esc(goal)}</div></div></div>`,
      );
    }

    if (guard) {
      const allowed = !!(guard.payload || {}).allowed;
      if (allowed) {
        parts.push(
          `<div class="bubble-row"><div class="sys-line" title="${esc(tooltipFor(guard))}">Input guard passed</div></div>`,
        );
      } else {
        const why = ((guard.payload || {}).reasons || []).join("; ") || data.message || "blocked";
        parts.push(
          `<div class="bubble-row"><div class="sys-line blocked" title="${esc(
            tooltipFor(guard),
          )}">Input guard blocked<span class="reason-inline">${esc(why)}</span></div></div>`,
        );
        root.innerHTML = parts.join("");
        return;
      }
    } else if (data.message && /blocked/i.test(data.message)) {
      parts.push(
        `<div class="bubble-row"><div class="sys-line blocked">Input guard blocked<span class="reason-inline">${esc(
          data.message,
        )}</span></div></div>`,
      );
      root.innerHTML = parts.join("");
      return;
    }

    if (explored.length) {
      const items = explored.map((file) => `<li class="mono">${esc(file)}</li>`).join("");
      parts.push(
        `<div class="bubble-row"><details class="explored"><summary>Explored ${explored.length} file${
          explored.length === 1 ? "" : "s"
        }</summary><ul>${items}</ul></details></div>`,
      );
    }

    if (workers.length) {
      const rows = workers
        .map((worker) => {
          const chip = worker.spinning
            ? `<span class="chip running"><span class="spinner" aria-hidden="true"></span>${esc(worker.state)}</span>`
            : `<span class="chip">${esc(worker.state)}</span>`;
          const action = worker.action
            ? `<span class="worker-action mono muted">${esc(worker.action)}</span>`
            : "";
          return `<button class="worker-row" type="button" data-task-id="${esc(worker.id)}">
            <span class="worker-title">${esc(worker.title)}</span>
            <span class="mono muted">${esc(worker.branch)}</span>
            ${chip}
            ${action}
          </button>`;
        })
        .join("");
      parts.push(
        `<div class="bubble-row"><div class="card orch-card plan-card">
          <div class="title">Started ${workers.length} workers, each in its own worktree</div>
          <div class="muted routed">Routed: ${esc(routingLabel(data.worker_model || (workers[0] && workers[0].model)))}</div>
          <div class="worker-list">${rows}</div>
        </div></div>`,
      );
    }

    if (data.awaiting && !startingIds) {
      const disabled = planChoice ? "disabled" : "";
      const note = planChoice ? `<div class="choice-note">${esc(planChoice)}</div>` : "";
      parts.push(
        `<div class="bubble-row"><div class="card orch-card plan-card">
          <div class="plan-head"><span class="dot orange"></span>Plan ready</div>
          <ul>${data.awaiting
            .map((task) => `<li>${esc(task.title)} · ${esc((task.allowed_paths || []).join(", "))}</li>`)
            .join("")}</ul>
          <div class="actions">
            <button class="btn primary" type="button" data-orch-approve ${disabled}>Approve plan</button>
            <button class="btn ghost" type="button" data-orch-cancel ${disabled}>Cancel</button>
          </div>
          ${note}
        </div></div>`,
      );
    }

    const stage = data.stage || "";
    const tasks = data.tasks || [];
    if (stage === "review" || stage === "done" || tasks.some((task) => task.lane === "review" || task.verdict)) {
      const reviewing = tasks.filter((task) => task.lane === "review" || task.verdict || task.status === "done");
      parts.push(
        `<div class="bubble-row"><div class="sys-line">Reviewing ${reviewing.length || tasks.length} branches</div></div>`,
      );
      tasks.forEach((task) => {
        if (!task.verdict && task.status !== "needs_review" && !conflictLabel(task)) return;
        const label =
          conflictLabel(task) ||
          (task.status === "needs_review" ? "needs_review" : task.verdict || task.status);
        const why = /conflict/.test(label) ? "" : task.reason || task.error || "";
        const chipClass = ["approve", "reject", "revise", "needs_review"].includes(label) ? label : "";
        parts.push(
          `<div class="bubble-row"><div class="verdict-row">
            <span class="dot ${verdictDot(label)}"></span>
            <strong>${esc(task.title)}</strong>
            <span class="chip verdict ${chipClass}">${esc(label)}</span>
          </div>${why ? `<div class="reason">${esc(why)}</div>` : ""}</div>`,
        );
      });
    }

    const integration = data.report && data.report.integration;
    if (integration) {
      const merged = integration.merged || [];
      const excluded = integration.excluded || [];
      const ex = excluded
        .map((item) => (typeof item === "string" ? item : `${item.task_id || "?"}: ${item.reason || "excluded"}`))
        .join("; ");
      parts.push(
        `<div class="bubble-row"><div class="sys-line">Merged ${merged.length}, excluded ${excluded.length}${
          ex ? ` (${esc(ex)})` : ""
        }</div></div>`,
      );
      const metrics = (data.report && data.report.metrics) || {};
      const checks = (integration.checks || [])
        .map((check) => `${check.name}${check.passed ? " pass" : " fail"}`)
        .join(", ");
      const wall = metrics.wall_seconds != null ? `${Number(metrics.wall_seconds).toFixed(1)}s` : "";
      const speedup = metrics.speedup != null ? String(metrics.speedup) : "";
      const fleet = metrics.fleet_speedup != null ? String(metrics.fleet_speedup) : "";
      const cost =
        metrics.total_usd != null
          ? `$${Number(metrics.total_usd).toFixed(4)}`
          : (metrics.costs || []).reduce((sum, row) => sum + (row.usd || 0), 0);
      const costText = typeof cost === "number" ? `$${cost.toFixed(4)}` : cost;
      const mergeCmd = `git -C ${data.repo || ""} merge ${integration.branch || ""}`;
      parts.push(
        `<div class="bubble-row"><div class="card orch-card summary-card">
          <div class="title">Integration summary</div>
          <p><span class="muted">Branch</span> <span class="mono">${esc(integration.branch || "")}</span></p>
          <p><span class="muted">Checks</span> ${esc(checks || "none")}</p>
          <p><span class="muted">Cost</span> ${esc(costText)} · <span class="muted">Wall</span> ${esc(
            wall,
          )} · <span class="muted">Speedup</span> ${esc(speedup)}x · <span class="muted">Fleet</span> ${esc(
            fleet,
          )}x</p>
          <div class="actions"><button class="btn ghost merge-btn" type="button" data-merge="${esc(
            mergeCmd,
          )}"><span class="merge-label">Copy merge command</span></button></div>
        </div></div>`,
      );
    }

    root.innerHTML = parts.join("") || `<p class="empty-line">No run yet. Describe a goal to begin.</p>`;

    root.querySelectorAll(".worker-row").forEach((button) => {
      button.addEventListener("click", () => {
        const task = (data.tasks || []).find((item) => item.id === button.dataset.taskId);
        if (task) selectTask(task, true);
        else if (data.awaiting) {
          const planned = data.awaiting.find((item) => item.id === button.dataset.taskId);
          if (planned) {
            selectTask(
              {
                id: planned.id,
                title: planned.title,
                branch: `tf/${data.run_id || "run"}/${planned.id}`,
                model: data.worker_model || "",
                lines: [],
              },
              true,
            );
          }
        }
      });
    });

    const approve = root.querySelector("[data-orch-approve]");
    const cancel = root.querySelector("[data-orch-cancel]");
    if (approve) {
      approve.addEventListener("click", () => {
        planChoice = "Approved";
        startingIds = (data.awaiting || []).map((task) => ({
          id: task.id,
          title: task.title,
          branch: `tf/${data.run_id || "run"}/${task.id}`,
          model: data.worker_model || "router",
        }));
        if (onPlanApproved) onPlanApproved();
        post(`/api/runs/${data.run_id}/approve`).finally(() => renderThread({ ...data, awaiting: null, stage: "fleet" }));
      });
    }
    if (cancel) {
      cancel.addEventListener("click", () => {
        planChoice = "Cancelled";
        startingIds = null;
        post(`/api/runs/${data.run_id}/cancel`).finally(() => renderThread(data));
      });
    }
    root.querySelectorAll("[data-merge]").forEach((button) => {
      button.addEventListener("click", async () => {
        const cmd = button.getAttribute("data-merge");
        if (cmd && navigator.clipboard) {
          try {
            await navigator.clipboard.writeText(cmd);
          } catch (_err) {
            /* ignore */
          }
        }
        button.classList.add("copied");
        button.innerHTML = `<span class="check" aria-hidden="true">✓</span><span class="merge-label">Copied</span>`;
        setTimeout(() => {
          button.classList.remove("copied");
          button.innerHTML = `<span class="merge-label">Copy merge command</span>`;
        }, 2000);
      });
    });

    if (stickToBottom || nearBottom) {
      root.scrollTop = root.scrollHeight;
      stickToBottom = true;
      document.getElementById("jump-latest").hidden = true;
    } else {
      document.getElementById("jump-latest").hidden = false;
    }
  }

  function bind() {
    const root = document.getElementById("orch-thread");
    const jump = document.getElementById("jump-latest");
    if (!root || !jump) return;
    root.addEventListener("scroll", () => {
      const nearBottom = root.scrollHeight - root.scrollTop - root.clientHeight < 80;
      stickToBottom = nearBottom;
      jump.hidden = nearBottom;
    });
    jump.addEventListener("click", () => {
      stickToBottom = true;
      root.scrollTop = root.scrollHeight;
      jump.hidden = true;
    });
    document.getElementById("follow-run").addEventListener("click", () => {
      const auto = document.getElementById("auto").getAttribute("aria-checked") === "true";
      const goal = document.getElementById("follow-goal").value;
      const model = document.getElementById("follow-model").value;
      post("/api/runs", { goal, worker_model: model, plan_approval: !auto })
        .then((res) => res.json())
        .then(() => {
          planChoice = null;
          startingIds = null;
          stickToBottom = true;
          document.getElementById("follow-goal").value = "";
        });
    });
  }

  function syncModels(models) {
    const select = document.getElementById("follow-model");
    if (!select || select.options.length) return;
    models.forEach((item) => {
      const option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.label;
      select.appendChild(option);
    });
  }

  function resetPlanChoice() {
    planChoice = null;
    startingIds = null;
  }

  return { renderThread, bind, syncModels, resetPlanChoice, clock, auth };
}
