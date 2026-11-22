/** Shared empty / skeleton / error panes — reuses transcript.css classes. */

import { setSafeText } from "./safe-text.js";

export function clearChildren(node) {
  while (node && node.firstChild) node.removeChild(node.firstChild);
}

/** Empty state: optional action button. Uses transcript-empty + optional btn. */
export function paintEmpty(host, { title, body, actionLabel, onAction } = {}) {
  clearChildren(host);
  const wrap = document.createElement("div");
  wrap.className = "pane-empty";
  wrap.dataset.state = "empty";
  if (title) {
    const h = document.createElement("h3");
    h.className = "type-title-lg";
    setSafeText(h, title);
    wrap.appendChild(h);
  }
  const p = document.createElement("p");
  p.className = "transcript-empty type-body-sm";
  setSafeText(p, body || "Nothing here yet.");
  wrap.appendChild(p);
  if (actionLabel && onAction) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn primary";
    btn.textContent = actionLabel;
    btn.addEventListener("click", onAction);
    wrap.appendChild(btn);
  }
  host.appendChild(wrap);
  return wrap;
}

/** Skeleton: reuses .transcript-skeleton .bar */
export function paintSkeleton(host, bars = 3) {
  clearChildren(host);
  const sk = document.createElement("div");
  sk.className = "transcript-skeleton";
  sk.dataset.skeleton = "1";
  sk.dataset.state = "loading";
  for (let i = 0; i < bars; i++) {
    const bar = document.createElement("div");
    bar.className = "bar";
    sk.appendChild(bar);
  }
  host.appendChild(sk);
  return sk;
}

/** Error banner with Retry — reuses .transcript-banner.is-error */
export function paintError(host, { message, onRetry } = {}) {
  clearChildren(host);
  const row = document.createElement("div");
  row.className = "transcript-banner is-error";
  row.dataset.state = "error";
  setSafeText(row, message || "Something went wrong");
  if (onRetry) {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "btn secondary";
    retry.textContent = "Retry";
    retry.addEventListener("click", onRetry);
    row.appendChild(retry);
  }
  host.appendChild(row);
  return row;
}
