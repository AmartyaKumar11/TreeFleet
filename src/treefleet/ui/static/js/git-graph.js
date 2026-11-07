/** Vertical git graph from board worktrees / tasks. Labels via textContent only. */

const LANE_COLORS = ["#4c8dff", "#f5a524", "#f5d76e", "#3ecf8e", "#c084fc", "#f07178"];

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

export function createGitGraph({ onSelect }) {
  function draw(data) {
    const host = document.getElementById("git-graph");
    if (!host) return;
    clear(host);
    const title = document.createElement("h2");
    title.textContent = "Git graph";
    host.appendChild(title);

    const tasks = (data.tasks || []).filter((task) => task.branch);
    const integration = data.report && data.report.integration;
    const base = (integration && integration.base) || data.head || "HEAD";
    const laneCount = Math.max(1, tasks.length);
    const width = Math.max(320, 80 + laneCount * 56);
    const height = Math.max(160, 72 + tasks.length * 48 + (integration ? 48 : 0));
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Task branches");

    function svgEl(name, attrs) {
      const node = document.createElementNS("http://www.w3.org/2000/svg", name);
      Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, String(value)));
      return node;
    }

    const baseY = 28;
    svg.appendChild(svgEl("line", { x1: 24, y1: baseY, x2: width - 24, y2: baseY, stroke: "rgba(255,255,255,.2)", "stroke-width": 2 }));
    const baseLabel = svgEl("text", { x: 24, y: baseY - 8, fill: "#c6c7cc", "font-size": 12 });
    baseLabel.textContent = `base ${base}`;
    svg.appendChild(baseLabel);

    tasks.forEach((task, index) => {
      const x = 48 + index * 56;
      const color = LANE_COLORS[index % LANE_COLORS.length];
      const endY = 72 + index * 48;
      svg.appendChild(
        svgEl("path", {
          d: `M${x} ${baseY} C${x} ${baseY + 24} ${x} ${endY - 24} ${x} ${endY}`,
          fill: "none",
          stroke: color,
          "stroke-width": 2,
        }),
      );
      const dot = svgEl("circle", {
        cx: x,
        cy: endY,
        r: 6,
        fill: color,
        "data-task-id": task.id,
        class: "graph-dot",
      });
      dot.style.cursor = "pointer";
      dot.addEventListener("click", () => onSelect && onSelect(task));
      svg.appendChild(dot);
      const label = svgEl("text", {
        x: x + 12,
        y: endY + 4,
        fill: "#c6c7cc",
        "font-size": 11,
        "data-task-id": task.id,
      });
      label.textContent = task.branch || task.title || task.id;
      label.style.cursor = "pointer";
      label.addEventListener("click", () => onSelect && onSelect(task));
      svg.appendChild(label);
    });

    if (integration && integration.branch) {
      const y = height - 28;
      const mid = 48 + Math.max(0, (tasks.length - 1) * 28);
      svg.appendChild(svgEl("circle", { cx: mid, cy: y, r: 7, fill: "#3ecf8e" }));
      const merge = svgEl("text", { x: mid + 14, y: y + 4, fill: "#c6c7cc", "font-size": 11 });
      merge.textContent = integration.branch;
      svg.appendChild(merge);
      tasks.forEach((_task, index) => {
        const x = 48 + index * 56;
        const startY = 72 + index * 48;
        svg.appendChild(
          svgEl("path", {
            d: `M${x} ${startY} C${x} ${startY + 20} ${mid} ${y - 20} ${mid} ${y}`,
            fill: "none",
            stroke: "rgba(62,207,142,.45)",
            "stroke-width": 1.5,
          }),
        );
      });
    }

    host.appendChild(svg);
  }

  return { draw };
}
