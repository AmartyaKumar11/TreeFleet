/** Vertical git graph. Labels are 12px mono beside lanes; caps at 8 with +N more. */

const LANE_COLORS = ["#4c8dff", "#f5a524", "#f5d76e", "#3ecf8e", "#c084fc", "#f07178", "#22d3ee", "#fb7185"];
const VISIBLE_CAP = 8;
const ROW = 28;
const LABEL_W = 240;
const LANE_X = 36;

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

export function createGitGraph({ onSelect }) {
  let expanded = false;
  let lastData = null;

  function draw(data) {
    lastData = data;
    const host = document.getElementById("git-graph");
    if (!host) return;
    clear(host);
    const title = document.createElement("h2");
    title.textContent = "Git graph";
    host.appendChild(title);

    const all = (data.tasks || []).filter((task) => task.branch);
    const integration = data.report && data.report.integration;
    const base = (integration && integration.base) || data.head || "HEAD";
    const capped = !expanded && all.length > VISIBLE_CAP;
    const tasks = capped ? all.slice(0, VISIBLE_CAP) : all;
    const extra = capped ? all.length - VISIBLE_CAP : 0;

    const rowCount = 1 + tasks.length + (extra ? 1 : 0) + (integration && integration.branch ? 1 : 0);
    const width = LANE_X + 16 + LABEL_W;
    const height = 20 + rowCount * ROW;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("width", String(width));
    svg.setAttribute("height", String(height));
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "Task branches");

    function svgEl(name, attrs) {
      const node = document.createElementNS("http://www.w3.org/2000/svg", name);
      Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, String(value)));
      return node;
    }

    function labelText(x, y, text, attrs) {
      const node = svgEl("text", {
        x,
        y,
        fill: "#c6c7cc",
        "font-size": "12",
        "font-family": "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
        ...attrs,
      });
      node.textContent = text;
      return node;
    }

    const baseY = 20;
    const endY = height - 12;
    svg.appendChild(
      svgEl("line", {
        x1: LANE_X,
        y1: baseY,
        x2: LANE_X,
        y2: endY,
        stroke: "rgba(255,255,255,.18)",
        "stroke-width": 2,
      }),
    );
    svg.appendChild(labelText(LANE_X + 14, baseY + 4, `base ${base}`));

    tasks.forEach((task, index) => {
      const y = 48 + index * ROW;
      const color = LANE_COLORS[index % LANE_COLORS.length];
      svg.appendChild(
        svgEl("path", {
          d: `M${LANE_X} ${baseY} V ${y}`,
          fill: "none",
          stroke: color,
          "stroke-width": 2,
          "pointer-events": "none",
        }),
      );
      const dot = svgEl("circle", {
        cx: LANE_X,
        cy: y,
        r: 5,
        fill: color,
        "data-task-id": task.id,
        class: "graph-dot",
      });
      dot.style.cursor = "pointer";
      dot.addEventListener("click", () => onSelect && onSelect(task));
      svg.appendChild(dot);
      const label = labelText(LANE_X + 14, y + 4, task.branch || task.title || task.id, {
        "data-task-id": task.id,
      });
      label.style.cursor = "pointer";
      label.addEventListener("click", () => onSelect && onSelect(task));
      svg.appendChild(label);
    });

    let y = 48 + tasks.length * ROW;
    if (extra) {
      const moreLabel = labelText(LANE_X + 14, y + 4, `+${extra} more`);
      moreLabel.setAttribute("data-graph-more", "1");
      moreLabel.style.cursor = "pointer";
      moreLabel.setAttribute("fill", "#4c8dff");
      moreLabel.addEventListener("click", () => {
        expanded = true;
        draw(lastData);
      });
      svg.appendChild(moreLabel);
      y += ROW;
    }

    if (integration && integration.branch) {
      svg.appendChild(svgEl("circle", { cx: LANE_X, cy: y, r: 6, fill: "#3ecf8e" }));
      svg.appendChild(labelText(LANE_X + 14, y + 4, integration.branch));
    }

    host.appendChild(svg);

    if (extra) {
      const hit = document.createElement("button");
      hit.type = "button";
      hit.className = "graph-more";
      hit.setAttribute("data-graph-more", "1");
      hit.textContent = `+${extra} more`;
      hit.addEventListener("click", () => {
        expanded = true;
        draw(lastData);
      });
      host.appendChild(hit);
    }
  }

  return { draw };
}
