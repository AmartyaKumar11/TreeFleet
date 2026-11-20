/** Git graph under the kanban. Cap 8 rows; collapse remembered in localStorage. */

import { statusToken } from "./status.js";

const CAP = 8;
const STORAGE_KEY = "treefleet.git-graph.collapsed";

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function cssToken(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function statusColor(task) {
  if (task.conflict) return cssToken("--status-error");
  return cssToken(statusToken(task));
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

function readCollapsed() {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch (_err) {
    return false;
  }
}

function writeCollapsed(value) {
  try {
    localStorage.setItem(STORAGE_KEY, value ? "1" : "0");
  } catch (_err) {
    /* ignore */
  }
}

function setCardHot(taskId, on) {
  document.querySelectorAll(".card.is-graph-hot").forEach((el) => el.classList.remove("is-graph-hot"));
  document.querySelectorAll(".graph-row.is-hot").forEach((el) => el.classList.remove("is-hot"));
  if (!on || !taskId) return;
  const card = document.querySelector(`.card[data-task-id="${CSS.escape(String(taskId))}"]`);
  if (card) card.classList.add("is-graph-hot");
  const row = document.querySelector(`.graph-row[data-task-id="${CSS.escape(String(taskId))}"]`);
  if (row) row.classList.add("is-hot");
}

export function createGitGraph({ onSelect }) {
  let expanded = false;
  let collapsed = readCollapsed();
  let last = null;
  let bound = false;
  let drawSig = "";

  function bindCrossHighlight() {
    if (bound) return;
    bound = true;
    document.addEventListener(
      "pointerover",
      (event) => {
        const card = event.target.closest?.(".card[data-task-id]");
        if (card) setCardHot(card.dataset.taskId, true);
        const row = event.target.closest?.(".graph-row[data-task-id]");
        if (row) setCardHot(row.dataset.taskId, true);
      },
      true,
    );
    document.addEventListener(
      "pointerout",
      (event) => {
        const from = event.target.closest?.(".card[data-task-id], .graph-row[data-task-id]");
        if (from) setCardHot(null, false);
      },
      true,
    );
    document.addEventListener("focusin", (event) => {
      const el = event.target.closest?.(".card[data-task-id], .graph-row[data-task-id]");
      if (el) setCardHot(el.dataset.taskId, true);
    });
    document.addEventListener("focusout", (event) => {
      const el = event.target.closest?.(".card[data-task-id], .graph-row[data-task-id]");
      if (el) setCardHot(null, false);
    });
  }

  function row(list, full, color, attrs) {
    const line = document.createElement("button");
    line.type = "button";
    line.className = "graph-row";
    if (attrs && attrs["data-graph-base"]) line.classList.add("is-base");
    if (attrs && attrs["data-graph-integrated"]) line.classList.add("is-integrated");
    const dot = document.createElement("span");
    dot.className = "graph-dot";
    dot.style.background = color;
    const label = document.createElement("span");
    label.className = "graph-label";
    label.textContent = middleTruncate(full);
    label.title = full;
    Object.entries(attrs || {}).forEach(([key, value]) => {
      // Attributes live on the row only so locators like [data-graph-integrated] stay unique.
      line.setAttribute(key, value);
      if (key === "data-task-id") {
        label.setAttribute(key, value);
        line.dataset.taskId = value;
      }
    });
    if (attrs && attrs["data-graph-integrated"]) {
      const check = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      check.setAttribute("class", "icon");
      check.setAttribute("aria-hidden", "true");
      const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
      use.setAttribute("href", "/static/icons.svg#icon-check");
      check.appendChild(use);
      label.prepend(check);
    }
    line.appendChild(dot);
    line.appendChild(label);
    if (attrs && attrs["data-task-id"]) {
      line.addEventListener("click", () => {
        const task = (last.tasks || []).find((item) => item.id === attrs["data-task-id"]);
        if (task && onSelect) onSelect(task);
      });
    }
    list.appendChild(line);
  }

  function draw(data) {
    last = data;
    bindCrossHighlight();
    const host = document.getElementById("git-graph");
    if (!host) return;
    const sig = JSON.stringify({
      collapsed,
      expanded,
      head: data.head,
      run: data.run_id,
      tasks: (data.tasks || []).map((task) => [task.id, task.branch, task.status, task.lane, task.verdict]),
      integration: data.report && data.report.integration,
      pr_mode: data.pr_mode,
    });
    if (sig === drawSig && host.querySelector(".graph-list, .graph-empty")) {
      host.classList.toggle("is-collapsed", collapsed);
      return;
    }
    drawSig = sig;
    clear(host);
    host.classList.toggle("is-collapsed", collapsed);

    const head = document.createElement("div");
    head.className = "git-graph-head";
    const title = document.createElement("h2");
    title.className = "graph-title type-micro";
    title.textContent = "Git graph";
    const divider = document.createElement("hr");
    divider.className = "graph-divider divider";
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "graph-toggle icon-btn";
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.setAttribute("aria-label", collapsed ? "Expand git graph" : "Collapse git graph");
    toggle.innerHTML = `<svg class="icon" aria-hidden="true"><use href="/static/icons.svg#icon-chevron-down"></use></svg>`;
    if (collapsed) toggle.querySelector("svg")?.style && (toggle.querySelector("svg").style.transform = "rotate(-90deg)");
    toggle.addEventListener("click", () => {
      collapsed = !collapsed;
      writeCollapsed(collapsed);
      draw(last);
    });
    head.appendChild(title);
    head.appendChild(divider);
    head.appendChild(toggle);
    host.appendChild(head);

    const all = (data.tasks || []).filter((task) => task.branch);
    const integration = data.report && data.report.integration;
    const base = (integration && integration.base) || data.head || "HEAD";
    const integrated = data.pr_mode
      ? ""
      : (integration && integration.branch) ||
        ((data.events || []).some((event) => event.type === "phase" && (event.payload || {}).name === "integrate")
          ? `tf/${data.run_id || "run"}/integrated`
          : "");

    if (!all.length && !integrated) {
      const empty = document.createElement("p");
      empty.className = "graph-empty type-caption";
      empty.textContent = "No branches yet";
      host.appendChild(empty);
      return;
    }

    const overflow = all.length > CAP;
    const shown = overflow && !expanded ? all.slice(0, CAP) : all;
    const hidden = overflow && !expanded ? all.length - CAP : 0;

    const list = document.createElement("div");
    list.className = "graph-list";
    row(list, `base ${base}`, cssToken("--border-strong") || cssToken("--muted"), { "data-graph-base": "1" });
    shown.forEach((task) => {
      row(list, task.branch || task.id, statusColor(task), { "data-task-id": task.id });
    });
    if (integrated) {
      row(list, integrated, cssToken("--status-success"), { "data-graph-integrated": "1" });
    }
    host.appendChild(list);

    if (overflow) {
      const hit = document.createElement("button");
      hit.type = "button";
      hit.className = "graph-more btn ghost";
      hit.setAttribute("data-graph-more", "1");
      hit.textContent = expanded ? "Show less" : `+${hidden} more`;
      hit.addEventListener("click", () => {
        expanded = !expanded;
        draw(last);
      });
      host.appendChild(hit);
    }
  }

  return { draw };
}
