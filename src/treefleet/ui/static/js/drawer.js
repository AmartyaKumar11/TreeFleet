/** Task drawer panes. Untrusted text goes through textContent / createTextNode only. */

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
  return rows.find((item) => item.task_id === task.id) || null;
}

export function createDrawer({ auth, worktreeFor }) {
  let followTail = true;
  let liveOffset = 0;
  let selected = null;
  let state = {};

  function setPane(id) {
    document.querySelectorAll(".drawer .tab").forEach((tab) => {
      const on = tab.dataset.pane === id;
      tab.setAttribute("aria-selected", String(on));
      tab.tabIndex = on ? 0 : -1;
    });
    document.querySelectorAll(".drawer .pane").forEach((pane) => {
      pane.hidden = pane.id !== id;
    });
  }

  function renderLive(task) {
    const root = document.getElementById("live");
    clear(root);
    const toolbar = el("div", "live-toolbar");
    const pause = el("button", "btn ghost", followTail ? "Pause" : "Follow");
    pause.type = "button";
    pause.addEventListener("click", () => {
      followTail = !followTail;
      pause.textContent = followTail ? "Pause" : "Follow";
      if (followTail) renderLive(task);
    });
    const copy = el("button", "btn ghost", "Copy");
    copy.type = "button";
    copy.addEventListener("click", async () => {
      const text = (task.lines || []).join("\n");
      try {
        if (navigator.clipboard) await navigator.clipboard.writeText(text);
        copy.textContent = "Copied";
        setTimeout(() => {
          copy.textContent = "Copy";
        }, 1200);
      } catch (_err) {
        /* ignore */
      }
    });
    toolbar.appendChild(pause);
    toolbar.appendChild(copy);
    root.appendChild(toolbar);

    const lines = task.lines || [];
    const total = lines.length;
    let start = liveOffset;
    if (followTail && total > LIVE_CAP) start = Math.max(0, total - LIVE_CAP);
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
    lines.slice(start).forEach((line) => {
      log.appendChild(el("div", "live-line", String(line)));
    });
    root.appendChild(log);
    if (followTail) log.scrollTop = log.scrollHeight;
  }

  function appendDiffLine(body, raw, index) {
    const row = el("div", "diff-line");
    const text = String(raw);
    if (text.startsWith("+") && !text.startsWith("+++")) row.classList.add("add");
    else if (text.startsWith("-") && !text.startsWith("---")) row.classList.add("del");
    else if (text.startsWith("@@")) row.classList.add("hunk");
    const ln = el("span", "ln", String(index + 1));
    const code = el("span", "code", text);
    row.appendChild(ln);
    row.appendChild(code);
    body.appendChild(row);
  }

  function renderDiffBody(root, data) {
    clear(root);
    const wrap = el("div", "diff-layout");
    const files = el("div", "diff-files");
    const view = el("div", "diff-view");
    const fileRows = data.files || data.diff_files || [];
    let active = 0;

    function showFile(index) {
      active = index;
      clear(view);
      [...files.children].forEach((child, i) => {
        child.classList.toggle("is-active", i === index);
      });
      const file = fileRows[index];
      if (!file) {
        view.appendChild(el("p", "empty-line", data.diff || data.error || "No diff."));
        return;
      }
      if (file.binary) {
        view.appendChild(el("p", "empty-line", `Binary file · ${file.path}`));
        return;
      }
      const raw = String(data.diff || "");
      const marker = `diff --git a/${file.path}`;
      let chunk = raw;
      if (raw.includes(marker)) {
        const start = raw.indexOf(marker);
        const next = raw.indexOf("\ndiff --git ", start + 1);
        chunk = next === -1 ? raw.slice(start) : raw.slice(start, next);
      }
      const lines = chunk.split("\n");
      const capped = lines.length > DIFF_CAP;
      const body = el("div", "diff-body");
      lines.slice(0, DIFF_CAP).forEach((line, i) => appendDiffLine(body, line, i));
      view.appendChild(body);
      if (capped) {
        const more = el("button", "btn ghost", "Show more");
        more.type = "button";
        more.addEventListener("click", () => {
          clear(body);
          lines.forEach((line, i) => appendDiffLine(body, line, i));
          more.remove();
        });
        view.appendChild(more);
      }
    }

    if (!fileRows.length) {
      const body = el("div", "diff-body");
      String(data.diff || data.error || "No diff.")
        .split("\n")
        .slice(0, DIFF_CAP)
        .forEach((line, i) => appendDiffLine(body, line, i));
      wrap.appendChild(body);
      root.appendChild(wrap);
      return;
    }

    fileRows.forEach((file, index) => {
      const btn = el("button", "diff-file", null);
      btn.type = "button";
      btn.appendChild(el("span", "path", file.path));
      const counts = el("span", "counts");
      if (file.binary) {
        counts.appendChild(el("span", "binary-tag", "binary"));
      } else {
        counts.appendChild(el("span", "add", `+${file.additions || 0}`));
        counts.appendChild(document.createTextNode(" "));
        counts.appendChild(el("span", "del", `-${file.deletions || 0}`));
      }
      btn.appendChild(counts);
      btn.addEventListener("click", () => showFile(index));
      files.appendChild(btn);
    });
    wrap.appendChild(files);
    wrap.appendChild(view);
    root.appendChild(wrap);
    showFile(active);
  }

  function renderDiff(task) {
    const root = document.getElementById("diff");
    clear(root);
    if (task.diff || task.diff_files) {
      renderDiffBody(root, { diff: task.diff || "", files: task.diff_files || [] });
      return;
    }
    const path = worktreeFor(task);
    root.appendChild(el("p", "empty-line", path ? "Loading diff…" : "No worktree for this task yet."));
    if (!path) return;
    fetch(`/api/diff?path=${encodeURIComponent(path)}`, { headers: auth() })
      .then((res) => res.json())
      .then((data) => {
        if (selected && selected.id !== task.id) return;
        renderDiffBody(root, data);
      })
      .catch(() => {
        clear(root);
        root.appendChild(el("p", "empty-line", "Diff unavailable."));
      });
  }

  function renderProof(task) {
    const root = document.getElementById("proofpane");
    clear(root);
    const proof = proofFrom(state, task);
    if (!proof) {
      root.appendChild(el("p", "empty-line", "Proof rows are in the isolation table."));
      return;
    }
    const table = el("table", "proof-table");
    const body = document.createElement("tbody");
    const rows = [
      ["Process cwd", proof.process_cwd_matches],
      ["Toplevel", proof.toplevel_matches],
      ["Registered", proof.registered_in_worktree_list],
      ["Canary absent", proof.canary_absent],
      ["Within scope", proof.changes_within_allowed_paths],
    ];
    rows.forEach(([label, ok]) => {
      const tr = document.createElement("tr");
      tr.appendChild(el("th", null, label));
      const td = el("td", ok ? "ok" : "bad", ok ? "yes ✓" : "NO ✗");
      tr.appendChild(td);
      body.appendChild(tr);
    });
    table.appendChild(body);
    root.appendChild(table);
    if (proof.contained_violation || (proof.reverted_paths && proof.reverted_paths.length)) {
      root.appendChild(el("p", "violation-title", "Contained violation · reverted paths"));
      const list = el("ul", "reverted-paths");
      (proof.reverted_paths || []).forEach((path) => {
        const item = document.createElement("li");
        item.textContent = String(path);
        list.appendChild(item);
      });
      root.appendChild(list);
    }
  }

  function renderChecks(task) {
    const root = document.getElementById("checks");
    clear(root);
    const checks = task.checks || [];
    if (!checks.length) {
      root.appendChild(el("p", "empty-line", "No checks for this task."));
      return;
    }
    checks.forEach((check) => {
      const row = el("div", "check-row");
      const head = el("div", "check-head");
      const status = el("span", check.passed ? "chip pass" : "chip fail", check.passed ? "pass" : "fail");
      head.appendChild(status);
      head.appendChild(el("strong", null, check.name || "check"));
      head.appendChild(
        el(
          "span",
          "muted",
          `exit ${check.exit_code ?? "?"} · ${check.duration_s != null ? `${check.duration_s}s` : "—"}`,
        ),
      );
      row.appendChild(head);
      if (check.output) {
        const toggle = el("button", "btn ghost", "Output");
        toggle.type = "button";
        const out = el("pre", "check-output");
        out.hidden = true;
        out.textContent = String(check.output);
        toggle.addEventListener("click", () => {
          out.hidden = !out.hidden;
        });
        row.appendChild(toggle);
        row.appendChild(out);
      }
      root.appendChild(row);
    });
  }

  function renderCost(task) {
    const root = document.getElementById("costpane");
    clear(root);
    const costs =
      (task.costs && task.costs.length && task.costs) ||
      (state.report && state.report.metrics && state.report.metrics.costs) ||
      [];
    if (!costs.length) {
      root.appendChild(el("p", "empty-line", "No cost rows yet."));
      return;
    }
    const table = el("table", "cost-table");
    const head = document.createElement("thead");
    const hr = document.createElement("tr");
    ["Role", "Tokens", "USD"].forEach((label) => hr.appendChild(el("th", null, label)));
    head.appendChild(hr);
    table.appendChild(head);
    const body = document.createElement("tbody");
    costs.forEach((row) => {
      const tr = document.createElement("tr");
      tr.appendChild(el("td", null, row.component || row.role || ""));
      tr.appendChild(el("td", "mono", row.tokens != null ? String(row.tokens) : "—"));
      const usd = document.createElement("td");
      if (row.label === "usage not reported" || (row.tokens == null && row.usd == null)) {
        usd.appendChild(el("span", "pill", "usage not reported"));
      } else if (row.label === "unpriced" || row.usd == null || row.usd === "") {
        usd.appendChild(el("span", "pill", "unpriced"));
      } else {
        usd.textContent = String(row.usd);
      }
      tr.appendChild(usd);
      body.appendChild(tr);
    });
    table.appendChild(body);
    root.appendChild(table);
  }

  function show(task, boardState, opts) {
    selected = task;
    state = boardState || {};
    if (!(opts && opts.keepOffset)) liveOffset = 0;
    if (!(opts && opts.keepFollow)) followTail = true;
    document.getElementById("drawer-empty").hidden = true;
    document.getElementById("drawer-task").hidden = false;
    document.getElementById("task-title").textContent = task.title || "";
    document.getElementById("task-meta").textContent = task.branch || "";
    renderLive(task);
    renderDiff(task);
    renderProof(task);
    renderChecks(task);
    renderCost(task);
  }

  function refresh(task, boardState) {
    if (!task) return;
    selected = task;
    state = boardState || state;
    const active = document.querySelector(".drawer .tab[aria-selected='true']");
    const pane = (active && active.dataset.pane) || "live";
    if (pane === "live") renderLive(task);
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
