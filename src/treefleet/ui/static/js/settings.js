/** Settings panel: client prefs in localStorage; read-only server info. */

import { TRANSCRIPT_MODE_KEY, DEFAULT_MODE } from "./transcript-model.js";
import { setSafeText } from "./safe-text.js";

const MOTION_KEY = "treefleet_reduce_motion";

function readMode() {
  try {
    const v = localStorage.getItem(TRANSCRIPT_MODE_KEY);
    if (v === "verbose" || v === "normal" || v === "summary") return v;
  } catch {
    /* ignore */
  }
  return DEFAULT_MODE;
}

function writeMode(mode) {
  localStorage.setItem(TRANSCRIPT_MODE_KEY, mode);
}

function readMotion() {
  try {
    return localStorage.getItem(MOTION_KEY) === "1";
  } catch {
    return false;
  }
}

function writeMotion(on) {
  localStorage.setItem(MOTION_KEY, on ? "1" : "0");
  document.documentElement.dataset.reduceMotion = on ? "1" : "0";
}

function applyMotionClass() {
  if (readMotion()) document.documentElement.dataset.reduceMotion = "1";
  else delete document.documentElement.dataset.reduceMotion;
}

export function createSettings({ getBoard, onModeChange, onOpenAuto }) {
  let open = false;
  let prevFocus = null;

  function panel() {
    return document.getElementById("settings-panel");
  }

  function syncSwitches() {
    const motion = document.getElementById("settings-motion");
    if (motion) motion.setAttribute("aria-checked", String(readMotion()));
    const mode = readMode();
    document.querySelectorAll("[data-settings-mode]").forEach((btn) => {
      const on = btn.dataset.settingsMode === mode;
      btn.setAttribute("aria-checked", String(on));
      btn.classList.toggle("is-active", on);
    });
    const board = getBoard ? getBoard() : {};
    const repo = document.getElementById("settings-repo");
    const branch = document.getElementById("settings-branch");
    if (repo) setSafeText(repo, board.repo || "—");
    if (branch) setSafeText(branch, board.branch || "—");
  }

  function openPanel() {
    const el = panel();
    if (!el) return;
    prevFocus = document.activeElement;
    open = true;
    el.hidden = false;
    document.getElementById("run-settings")?.setAttribute("aria-expanded", "true");
    document.getElementById("side-settings")?.setAttribute("aria-expanded", "true");
    syncSwitches();
    const first = el.querySelector("button, [href], input, select, textarea");
    if (first) first.focus();
  }

  function close() {
    const el = panel();
    if (!el) return;
    open = false;
    el.hidden = true;
    document.getElementById("run-settings")?.setAttribute("aria-expanded", "false");
    document.getElementById("side-settings")?.setAttribute("aria-expanded", "false");
    if (prevFocus && prevFocus.focus) prevFocus.focus();
    prevFocus = null;
  }

  function toggle() {
    if (open) close();
    else openPanel();
  }

  function bind() {
    applyMotionClass();
    ["run-settings", "side-settings"].forEach((id) => {
      const btn = document.getElementById(id);
      if (!btn) return;
      btn.setAttribute("aria-controls", "settings-panel");
      btn.addEventListener("click", (event) => {
        event.stopPropagation();
        toggle();
      });
    });
    const closeBtn = document.getElementById("settings-close");
    if (closeBtn) closeBtn.addEventListener("click", close);

    const motion = document.getElementById("settings-motion");
    if (motion) {
      motion.addEventListener("click", () => {
        const next = motion.getAttribute("aria-checked") !== "true";
        motion.setAttribute("aria-checked", String(next));
        writeMotion(next);
      });
    }

    document.querySelectorAll("[data-settings-mode]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const mode = btn.dataset.settingsMode;
        writeMode(mode);
        syncSwitches();
        if (onModeChange) onModeChange(mode);
      });
    });

    const auto = document.getElementById("settings-auto");
    const legacy = document.getElementById("auto");
    if (auto) {
      auto.addEventListener("click", () => {
        const next = auto.getAttribute("aria-checked") !== "true";
        auto.setAttribute("aria-checked", String(next));
        if (legacy) legacy.setAttribute("aria-checked", String(next));
        if (onOpenAuto) onOpenAuto(next);
      });
    }

    document.addEventListener("keydown", (event) => {
      if (!open) return;
      if (event.key === "Escape") {
        event.preventDefault();
        close();
      }
    });
    document.addEventListener("click", (event) => {
      if (!open) return;
      const el = panel();
      const t = event.target;
      if (el && !el.contains(t) && t !== document.getElementById("run-settings") && t !== document.getElementById("side-settings")) {
        // allow clicks on buttons that open settings
        if (t && (t.closest("#run-settings") || t.closest("#side-settings"))) return;
        close();
      }
    });
  }

  return {
    bind,
    open: openPanel,
    close,
    isOpen: () => open,
    sync: syncSwitches,
    readMode,
  };
}
