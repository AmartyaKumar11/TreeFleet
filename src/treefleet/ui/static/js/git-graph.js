/** Fixed-width git graph. One expand control, labels ellipsize in CSS. */

const CAP = 8;

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function statusColor(task) {
  const text = `${task.status || ""} ${task.verdict || ""} ${task.reason || ""}`.toLowerCase();
  if (task.conflict || task.status === "failed" || text.includes("conflict")) return "var(--red)";
  if (task.lane === "needs" || task.status === "needs_review") return "var(--orange)";
  if (task.lane === "review") return "var(--yellow)";
  if (task.lane === "ready" || task.status === "done" || task.verdict === "approve") return "var(--green)";
  return "var(--blue)";
}

export function createGitGraph({ onSelect }) {
  let expanded = false;
  let last = null;

  function row(list, full, color, attrs) {
    const line = document.createElement("div");
    line.className = "graph-row";
    const dot = document.createElement("span");
    dot.className = "graph-dot";
    dot.style.background = color;
    const label = document.createElement("span");
    label.className = "graph-label";
    label.textContent = full;
    label.title = full;
    Object.entries(attrs || {}).forEach(([key, value]) => {
      label.setAttribute(key, value);
      if (key === "data-task-id") line.dataset.taskId = value;
    });
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
    const host = document.getElementById("git-graph");
    if (!host) return;
    clear(host);
    const title = document.createElement("h2");
    title.textContent = "Git graph";
    host.appendChild(title);

    const all = (data.tasks || []).filter((task) => task.branch);
    const integration = data.report && data.report.integration;
    const base = (integration && integration.base) || data.head || "HEAD";
    const integrated =
      (integration && integration.branch) ||
      ((data.events || []).some((event) => event.type === "phase" && (event.payload || {}).name === "integrate")
        ? `tf/${data.run_id || "run"}/integrated`
        : "");
    const overflow = all.length > CAP;
    const shown = overflow && !expanded ? all.slice(0, CAP) : all;
    const hidden = overflow && !expanded ? all.length - CAP : 0;

    const list = document.createElement("div");
    list.className = "graph-list";
    row(list, `base ${base}`, "#2a313c", { "data-graph-base": "1" });
    shown.forEach((task) => {
      row(list, task.branch || task.id, statusColor(task), { "data-task-id": task.id });
    });
    if (integrated) {
      row(list, integrated, "var(--green)", { "data-graph-integrated": "1" });
    }
    host.appendChild(list);

    if (overflow) {
      const hit = document.createElement("button");
      hit.type = "button";
      hit.className = "graph-more";
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
