/** Ctrl/Cmd+K command palette with fuzzy filter. */

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
}

function fuzzy(query, text) {
  const q = String(query || "").toLowerCase().trim();
  const t = String(text || "").toLowerCase();
  if (!q) return true;
  let i = 0;
  for (const ch of t) {
    if (ch === q[i]) i += 1;
    if (i >= q.length) return true;
  }
  return false;
}

export function createPalette({
  onNewRun,
  onJumpTask,
  onSwitchView,
  onToggleAuto,
  onCopyMerge,
  getTasks,
}) {
  let open = false;
  let active = 0;
  let items = [];

  function root() {
    return document.getElementById("command-palette");
  }

  function commands() {
    const rows = [
      { id: "new-run", label: "New run", run: onNewRun },
      { id: "switch-view", label: "Switch view", run: onSwitchView },
      { id: "toggle-auto", label: "Toggle auto-run", run: onToggleAuto },
      { id: "copy-merge", label: "Copy merge command", run: onCopyMerge },
    ];
    (getTasks() || []).forEach((task) => {
      rows.push({
        id: `task-${task.id}`,
        label: `Jump to task · ${task.title || task.id}`,
        run: () => onJumpTask(task),
      });
    });
    return rows;
  }

  function renderList(query) {
    const list = document.getElementById("command-list");
    clear(list);
    items = commands().filter((item) => fuzzy(query, item.label));
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "palette-empty";
      empty.textContent = "No matches";
      list.appendChild(empty);
      return;
    }
    if (active >= items.length) active = 0;
    items.forEach((item, index) => {
      const option = document.createElement("div");
      option.className = "palette-item" + (index === active ? " is-active" : "");
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", String(index === active));
      option.id = `palette-opt-${index}`;
      option.textContent = item.label;
      option.addEventListener("click", () => {
        close();
        item.run();
      });
      list.appendChild(option);
    });
    const input = document.getElementById("command-input");
    if (input) input.setAttribute("aria-activedescendant", `palette-opt-${active}`);
  }

  function openPalette() {
    const el = root();
    if (!el) return;
    open = true;
    el.hidden = false;
    active = 0;
    const input = document.getElementById("command-input");
    if (input) {
      input.value = "";
      input.focus();
    }
    renderList("");
  }

  function close() {
    const el = root();
    if (!el) return;
    open = false;
    el.hidden = true;
  }

  function isOpen() {
    return open;
  }

  function bind() {
    document.addEventListener("keydown", (event) => {
      const mod = event.metaKey || event.ctrlKey;
      if (mod && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (open) close();
        else openPalette();
        return;
      }
      if (!open) return;
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        active = Math.min(items.length - 1, active + 1);
        renderList(document.getElementById("command-input").value);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        active = Math.max(0, active - 1);
        renderList(document.getElementById("command-input").value);
      } else if (event.key === "Enter") {
        event.preventDefault();
        const item = items[active];
        if (item) {
          close();
          item.run();
        }
      }
    });
    const input = document.getElementById("command-input");
    if (input) {
      input.addEventListener("input", () => {
        active = 0;
        renderList(input.value);
      });
    }
    const backdrop = document.getElementById("command-backdrop");
    if (backdrop) backdrop.addEventListener("click", close);
  }

  return { bind, open: openPalette, close, isOpen };
}
