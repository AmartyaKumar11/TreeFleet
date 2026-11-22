/**
 * Pure transcript derivation from /api/transcript items + plan + board snapshot.
 * No DOM. Levels: summary < normal < verbose (an entry shows when mode ≥ its level).
 */

export const TRANSCRIPT_MODE_KEY = "treefleet_transcript_mode";
export const DEFAULT_MODE = "normal";
export const POLL_INTERVAL_MS = 1000;
export const WINDOW_NODE_CAP = 400;
export const INITIAL_PAGE_CAP = 40;
export const PAGE_LIMIT = 500;

/** Join rule for consecutive output_line fragments (see audit cursor-and-output-20.jsonl). */
export const ASSISTANT_JOIN = Object.freeze({
  name: "space-unless-tight",
  /** Next fragment starts with whitespace or tight punctuation/affix → no inserted space. */
  tightNext: /^[\s.,;:!?\)\]\}'"`_\-]/u,
  join(parts) {
    let out = "";
    for (const raw of parts) {
      const part = String(raw == null ? "" : raw);
      if (!part) continue;
      if (!out) {
        out = part;
        continue;
      }
      if (/\s$/u.test(out) || ASSISTANT_JOIN.tightNext.test(part)) out += part;
      else out += ` ${part}`;
    }
    return out;
  },
});

const LEVEL_RANK = { summary: 0, normal: 1, verbose: 2 };

export function modeRank(mode) {
  return LEVEL_RANK[mode] ?? LEVEL_RANK.normal;
}

export function visibleInMode(entry, mode) {
  if (!entry) return false;
  return modeRank(mode) >= modeRank(entry.level || "verbose");
}

function payload(item) {
  return item && typeof item.payload === "object" && item.payload ? item.payload : {};
}

function entryId(offset, suffix = "") {
  return `e:${offset}${suffix ? `:${suffix}` : ""}`;
}

function base(item, kind, level, extra = {}) {
  const offset = item.offset != null ? Number(item.offset) : Number(item.seq) || 0;
  return {
    id: entryId(offset, extra.idSuffix || ""),
    kind,
    level,
    actor: extra.actor || item.source || "system",
    task_id: item.task_id || null,
    ts: item.ts || null,
    offset,
    sourceType: item.type || "",
    ...extra.fields,
  };
}

function toolVerb(tool) {
  const t = String(tool || "").toLowerCase();
  if (t === "read" || t === "list") return "Read";
  if (t === "edit" || t === "write") return "Edited";
  if (t === "delete") return "Deleted";
  if (t === "shell" || t === "bash" || t === "terminal") return "Ran";
  if (t === "grep" || t === "search" || t === "glob") return "Searched";
  return "Used";
}

function toolNoun(tool, n) {
  const t = String(tool || "").toLowerCase();
  if (t === "read" || t === "list") return n === 1 ? "file" : "files";
  if (t === "edit" || t === "write") return n === 1 ? "file" : "files";
  if (t === "delete") return n === 1 ? "file" : "files";
  if (t === "shell" || t === "bash" || t === "terminal") return n === 1 ? "command" : "commands";
  if (t === "grep" || t === "search" || t === "glob") return n === 1 ? "pattern" : "patterns";
  return n === 1 ? "tool" : "tools";
}

function tsMs(ts) {
  if (!ts) return null;
  const t = Date.parse(ts);
  return Number.isFinite(t) ? t : null;
}

/** True when timestamps carry sub-second precision (fractional seconds in ISO). */
export function hasSubsecondTs(ts) {
  return typeof ts === "string" && /\.\d+/.test(ts);
}

function durationMs(startTs, endTs) {
  if (!hasSubsecondTs(startTs) || !hasSubsecondTs(endTs)) return null;
  const a = tsMs(startTs);
  const b = tsMs(endTs);
  if (a == null || b == null || b < a) return null;
  return b - a;
}

/**
 * Build derived transcript entries.
 * @param {object[]} items raw /api/transcript items (with offset)
 * @param {object|null} plan plan=1 payload
 * @param {object} board board snapshot (goal, report, stage, …)
 */
export function buildTranscriptEntries(items, plan, board = {}) {
  const list = Array.isArray(items) ? items : [];
  const entries = [];
  const goalText = (plan && plan.goal) || board.goal || (board.report && board.report.goal) || "";
  if (goalText) {
    entries.push({
      id: "user_goal",
      kind: "user_goal",
      level: "summary",
      actor: "you",
      task_id: null,
      ts: list[0] && list[0].ts,
      offset: -1,
      text: String(goalText),
    });
  }

  if (plan && (plan.rationale || (plan.tasks && plan.tasks.length))) {
    entries.push({
      id: "plan",
      kind: "plan",
      level: "summary",
      actor: "orchestrator",
      task_id: null,
      ts: null,
      offset: -2,
      rationale: String(plan.rationale || ""),
      tasks: (plan.tasks || []).map((task) => ({
        id: task.id,
        title: task.title,
        allowed_paths: task.allowed_paths || [],
        instructions: task.instructions || "",
        depends_on: task.depends_on || [],
        checks: task.checks || [],
      })),
    });
  }

  // Plan approval card state from events
  let approval = null;
  for (const item of list) {
    if (item.type === "awaiting_approval") {
      approval = {
        id: "plan_approval",
        kind: "plan_approval",
        level: "summary",
        actor: "orchestrator",
        task_id: null,
        ts: item.ts,
        offset: item.offset != null ? Number(item.offset) : 0,
        status: "pending",
        source: null,
      };
    } else if (item.type === "approval.granted" && approval) {
      approval = {
        ...approval,
        status: "approved",
        source: payload(item).source || "http",
        ts: item.ts,
      };
    } else if (item.type === "approval.denied" && approval) {
      approval = {
        ...approval,
        status: "rejected",
        source: payload(item).source || "http",
        ts: item.ts,
      };
    }
  }
  if (board.awaiting && (!approval || approval.status === "pending")) {
    approval = {
      id: "plan_approval",
      kind: "plan_approval",
      level: "summary",
      actor: "orchestrator",
      task_id: null,
      ts: null,
      offset: -3,
      status: "pending",
      source: null,
      awaiting: board.awaiting,
    };
  }
  if (approval) entries.push(approval);

  // Pair tools and join assistant lines in one forward pass
  const openTools = new Map(); // key -> start item
  let assistantBuf = null;

  function flushAssistant() {
    if (!assistantBuf || !assistantBuf.parts.length) {
      assistantBuf = null;
      return;
    }
    entries.push({
      id: entryId(assistantBuf.firstOffset, "assistant"),
      kind: "assistant_message",
      level: "normal",
      actor: "worker",
      task_id: assistantBuf.task_id,
      ts: assistantBuf.ts,
      offset: assistantBuf.firstOffset,
      text: ASSISTANT_JOIN.join(assistantBuf.parts),
      lineCount: assistantBuf.parts.length,
    });
    assistantBuf = null;
  }

  function toolKey(taskId, tool, path) {
    return `${taskId || ""}|${tool || ""}|${path || ""}`;
  }

  for (const item of list) {
    const type = String(item.type || "");
    const body = payload(item);

    if (type === "output_line") {
      const line = String(body.line || "");
      if (!line) continue;
      if (
        assistantBuf &&
        assistantBuf.task_id === (item.task_id || null)
      ) {
        assistantBuf.parts.push(line);
      } else {
        flushAssistant();
        assistantBuf = {
          task_id: item.task_id || null,
          ts: item.ts,
          firstOffset: item.offset != null ? Number(item.offset) : 0,
          parts: [line],
        };
      }
      continue;
    }

    if (type === "cursor.tool_start" || type === "cursor.tool_finish") {
      flushAssistant();
    } else if (assistantBuf && item.task_id && assistantBuf.task_id !== item.task_id) {
      flushAssistant();
    }

    if (type === "cursor.tool_start") {
      const key = toolKey(item.task_id, body.tool, body.path);
      openTools.set(key, item);
      // Placeholder; may be replaced when finish arrives. Keep for unmatched/in-progress.
      entries.push({
        id: entryId(item.offset != null ? Number(item.offset) : 0, "tool"),
        kind: "tool_call",
        level: "verbose",
        actor: "worker",
        task_id: item.task_id || null,
        ts: item.ts,
        offset: item.offset != null ? Number(item.offset) : 0,
        tool: body.tool || "",
        path: body.path || "",
        detail: body.detail || "",
        ok: null,
        duration_ms: null,
        inProgress: true,
        _toolKey: key,
      });
      continue;
    }

    if (type === "cursor.tool_finish") {
      const key = toolKey(item.task_id, body.tool, body.path);
      const start = openTools.get(key);
      openTools.delete(key);
      const startOff = start && start.offset != null ? Number(start.offset) : item.offset != null ? Number(item.offset) : 0;
      const existing = entries.findIndex((e) => e.kind === "tool_call" && e._toolKey === key && e.inProgress);
      const row = {
        id: entryId(startOff, "tool"),
        kind: "tool_call",
        level: "verbose",
        actor: "worker",
        task_id: item.task_id || null,
        ts: (start && start.ts) || item.ts,
        offset: startOff,
        tool: body.tool || (start && payload(start).tool) || "",
        path: body.path || (start && payload(start).path) || "",
        detail: (start && payload(start).detail) || "",
        ok: body.ok,
        duration_ms: start ? durationMs(start.ts, item.ts) : null,
        inProgress: false,
        _toolKey: key,
      };
      if (existing >= 0) entries[existing] = row;
      else entries.push(row);
      continue;
    }

    if (type === "model_selected") {
      flushAssistant();
      entries.push(
        base(item, "model_selected", "normal", {
          fields: { model: body.model, source: body.source, complexity: body.complexity },
        }),
      );
      continue;
    }
    if (type === "review_started") {
      flushAssistant();
      entries.push(base(item, "review_started", "normal", { fields: { round: body.round } }));
      continue;
    }
    if (type === "reaction.fired") {
      flushAssistant();
      entries.push(
        base(item, "reaction", "normal", {
          fields: {
            event: body.event,
            action: body.action,
            attempt: body.attempt,
            escalate: body.escalate,
          },
        }),
      );
      continue;
    }
    if (type === "guard.decision") {
      flushAssistant();
      const allowed = !!body.allowed;
      entries.push(
        base(item, "guard", allowed ? "verbose" : "normal", {
          fields: {
            allowed,
            stage: body.stage,
            reasons: body.reasons || [],
            warning: !allowed,
          },
        }),
      );
      continue;
    }
    if (
      type === "phase" ||
      type === "orchestrator.agent_start" ||
      type === "orchestrator.agent_end" ||
      type === "orchestrator.model_call" ||
      type === "worker.ready" ||
      type === "worker.spawned" ||
      type === "worker.step" ||
      type === "worker.step_callback" ||
      type === "worker.task_callback"
    ) {
      flushAssistant();
      entries.push(
        base(item, "system_verbose", "verbose", {
          fields: { label: type, body },
        }),
      );
      continue;
    }
    if (type === "worker.exited") {
      flushAssistant();
      const files = Array.isArray(body.files) ? body.files : [];
      const err = String(body.error || "");
      entries.push(
        base(item, "worker_exited", "summary", {
          fields: {
            status: body.status,
            files,
            fileCount: files.length,
            error: err,
            warning: !!err || body.status === "failed",
          },
        }),
      );
      continue;
    }
    if (type === "review_verdict") {
      flushAssistant();
      entries.push(
        base(item, "review_verdict", "summary", {
          fields: {
            verdict: body.verdict,
            reason: body.reason || "",
            suggestions: body.suggestions || [],
            round: body.round,
          },
        }),
      );
      continue;
    }
    if (type.startsWith("pr.")) {
      flushAssistant();
      entries.push(base(item, "pr", "summary", { fields: { prType: type, body } }));
      continue;
    }
    if (
      type === "tool.error" ||
      type === "tool.arg_error" ||
      type === "protocol_error" ||
      type === "guard_degraded"
    ) {
      flushAssistant();
      entries.push(
        base(item, "error", "summary", {
          fields: {
            errorType: type,
            message: body.message || body.detector || type,
            tool: body.tool,
            warning: type === "tool.arg_error" || type === "guard_degraded",
          },
        }),
      );
      continue;
    }
    if (type === "awaiting_approval" || type === "approval.granted" || type === "approval.denied") {
      // Handled in approval card pass.
      continue;
    }
    // Unknown — verbose only
    flushAssistant();
    entries.push(
      base(item, "unknown", "verbose", {
        fields: { label: type || "unknown", body },
      }),
    );
  }
  flushAssistant();

  // Normal mode: collapse consecutive tool_call rows of one task into tool_group
  const withGroups = [];
  let toolRun = [];
  function flushTools() {
    if (!toolRun.length) return;
    if (toolRun.length === 1) {
      const one = { ...toolRun[0] };
      delete one._toolKey;
      withGroups.push(one);
      toolRun = [];
      return;
    }
    const counts = new Map();
    for (const row of toolRun) {
      const verb = toolVerb(row.tool);
      counts.set(verb, (counts.get(verb) || 0) + 1);
    }
    const summary = [...counts.entries()]
      .map(([verb, n]) => {
        const sample = toolRun.find((r) => toolVerb(r.tool) === verb);
        return `${verb} ${n} ${toolNoun(sample && sample.tool, n)}`;
      })
      .join(", ");
    withGroups.push({
      id: `group:${toolRun[0].id}`,
      kind: "tool_group",
      level: "normal",
      actor: "worker",
      task_id: toolRun[0].task_id,
      ts: toolRun[0].ts,
      offset: toolRun[0].offset,
      summary,
      children: toolRun.map((row) => {
        const copy = { ...row };
        delete copy._toolKey;
        return copy;
      }),
    });
    toolRun = [];
  }
  for (const entry of entries) {
    if (entry.kind === "tool_call") {
      if (toolRun.length && toolRun[0].task_id !== entry.task_id) flushTools();
      toolRun.push(entry);
    } else {
      flushTools();
      if (entry._toolKey) delete entry._toolKey;
      withGroups.push(entry);
    }
  }
  flushTools();

  const integration = board.report && board.report.integration;
  if (integration) {
    withGroups.push({
      id: "integration",
      kind: "integration",
      level: "summary",
      actor: "orchestrator",
      task_id: null,
      ts: null,
      offset: Number.MAX_SAFE_INTEGER,
      merged: integration.merged || [],
      excluded: integration.excluded || [],
      branch: integration.branch || "",
      checks: integration.checks || [],
      metrics: (board.report && board.report.metrics) || {},
    });
  }

  return withGroups;
}

export function filterByMode(entries, mode) {
  const out = [];
  for (const entry of entries) {
    if (entry.kind === "tool_group") {
      if (mode === "verbose") {
        for (const child of entry.children || []) {
          if (visibleInMode(child, mode)) out.push(child);
        }
      } else if (visibleInMode(entry, mode)) {
        out.push(entry);
      }
      continue;
    }
    if (entry.kind === "tool_call" && mode === "normal") {
      // Lone tool rows still show in normal as a one-item group appearance
      out.push(entry);
      continue;
    }
    if (visibleInMode(entry, mode)) out.push(entry);
  }
  return out;
}
