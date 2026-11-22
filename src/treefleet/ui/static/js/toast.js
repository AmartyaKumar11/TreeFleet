/** Stacked toasts: info/success auto-dismiss; warning/error persist. */

import { setSafeText } from "./safe-text.js";

const MAX = 3;
const AUTO_MS = 5000;
const KIND_LABEL = {
  info: "Info",
  success: "Success",
  warning: "Warning",
  error: "Error",
};

let stack = [];
let seq = 0;

function ensureHost() {
  let host = document.getElementById("toast-host");
  if (host) return host;
  host = document.createElement("div");
  host.id = "toast-host";
  host.className = "toast-host";
  host.setAttribute("aria-live", "polite");
  host.setAttribute("aria-relevant", "additions");
  document.body.appendChild(host);
  return host;
}

function clearTimer(item) {
  if (item.timer) {
    clearTimeout(item.timer);
    item.timer = null;
  }
}

function schedule(item) {
  clearTimer(item);
  if (item.kind !== "info" && item.kind !== "success") return;
  item.timer = setTimeout(() => dismiss(item.id), AUTO_MS);
}

function dismiss(id) {
  const idx = stack.findIndex((t) => t.id === id);
  if (idx < 0) return;
  const [item] = stack.splice(idx, 1);
  clearTimer(item);
  if (item.node && item.node.parentNode) item.node.remove();
}

function paint(item) {
  const node = document.createElement("div");
  node.className = `toast-item toast-${item.kind}`;
  node.dataset.toastId = String(item.id);
  if (item.kind === "error") node.setAttribute("role", "alert");
  else node.setAttribute("role", "status");

  const icon = document.createElement("span");
  icon.className = "toast-kind";
  icon.textContent = KIND_LABEL[item.kind] || "Info";
  node.appendChild(icon);

  const body = document.createElement("div");
  body.className = "toast-body";
  const title = document.createElement("div");
  title.className = "toast-title";
  setSafeText(title, item.title || "");
  body.appendChild(title);
  if (item.detail) {
    const detail = document.createElement("div");
    detail.className = "toast-detail";
    setSafeText(detail, item.detail);
    body.appendChild(detail);
  }
  if (item.action && item.action.label && item.action.run) {
    const act = document.createElement("button");
    act.type = "button";
    act.className = "toast-action btn ghost";
    act.textContent = item.action.label;
    act.addEventListener("click", () => {
      item.action.run();
      dismiss(item.id);
    });
    body.appendChild(act);
  }
  node.appendChild(body);

  const close = document.createElement("button");
  close.type = "button";
  close.className = "toast-dismiss icon-btn";
  close.setAttribute("aria-label", "Dismiss notification");
  close.textContent = "×";
  close.addEventListener("click", () => dismiss(item.id));
  node.appendChild(close);

  const pause = () => clearTimer(item);
  const resume = () => schedule(item);
  node.addEventListener("mouseenter", pause);
  node.addEventListener("mouseleave", resume);
  node.addEventListener("focusin", pause);
  node.addEventListener("focusout", resume);

  item.node = node;
  return node;
}

function show(opts = {}) {
  const kind = ["info", "success", "warning", "error"].includes(opts.kind) ? opts.kind : "info";
  const item = {
    id: ++seq,
    kind,
    title: opts.title || KIND_LABEL[kind],
    detail: opts.detail || "",
    action: opts.action || null,
    timer: null,
    node: null,
  };
  while (stack.length >= MAX) {
    dismiss(stack[stack.length - 1].id);
  }
  stack.unshift(item);
  const host = ensureHost();
  const node = paint(item);
  host.insertBefore(node, host.firstChild);
  schedule(item);
  return item.id;
}

export const toast = {
  show,
  dismiss,
  _stack: () => stack.slice(),
  _reset() {
    while (stack.length) dismiss(stack[0].id);
    seq = 0;
  },
};
