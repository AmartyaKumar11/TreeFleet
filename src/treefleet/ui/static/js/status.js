/** Shared status → token / class map for lane dots, badges, drawer, run history. */

const TOKEN = {
  running: "--accent",
  needs: "--status-needs",
  review: "--status-warning",
  ready: "--status-success",
  failed: "--status-error",
  stopped: "--muted",
  muted: "--muted",
};

const CLASS = {
  running: "is-running accent",
  needs: "is-needs needs",
  review: "is-review yellow",
  ready: "is-success green",
  failed: "is-error red",
  stopped: "is-stopped muted",
  muted: "muted",
};

const LABEL = {
  running: "running",
  needs: "needs you",
  review: "in review",
  ready: "ready",
  failed: "failed",
  stopped: "stopped",
  muted: "idle",
};

export function statusKey(task) {
  if (!task || typeof task !== "object") return "muted";
  const s = String(task.status || "").toLowerCase();
  const lane = String(task.lane || "").toLowerCase();
  const awaiting = /awaiting|needs.?you|needs_human/.test(s) || task.needs_human;
  if (awaiting || s === "needs_review" || lane === "needs") return "needs";
  if (s === "failed" || s === "timed_out" || s === "error" || s === "blocked") return "failed";
  if (s === "cancelled" || s === "canceled" || s === "stopped") return "stopped";
  if (lane === "ready" || task.verdict === "approve" || s === "done" || s === "success") return "ready";
  if (lane === "review" || s === "in_review" || s === "review") return "review";
  if (s === "running" || lane === "working" || task.action) return "running";
  return "muted";
}

export function statusToken(keyOrTask) {
  const key = typeof keyOrTask === "string" ? keyOrTask : statusKey(keyOrTask);
  return TOKEN[key] || TOKEN.muted;
}

export function statusClass(keyOrTask) {
  const key = typeof keyOrTask === "string" ? keyOrTask : statusKey(keyOrTask);
  return CLASS[key] || CLASS.muted;
}

export function statusLabel(keyOrTask) {
  const key = typeof keyOrTask === "string" ? keyOrTask : statusKey(keyOrTask);
  return LABEL[key] || LABEL.muted;
}

export function statusWords(task) {
  return statusLabel(task);
}
