"""Render eval/report.md and eval/report.json. No agent framework."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def estimates(rows: list[dict[str, Any]], cap: int) -> list[str]:
    """Extrapolate from measured rows. This is not a measurement."""
    source = [row for row in rows if int(row.get("task_count") or 0) >= 1 and float(row.get("sequential_s") or 0) > 0]
    if not source:
        return ["No measured run, so there is no projection."]
    tasks = sum(int(row.get("task_count") or 0) for row in source)
    sequential = sum(float(row.get("sequential_s") or 0) for row in source)
    cost = sum(float(row.get("cost_usd") or 0) for row in source)
    per_task = sequential / tasks
    per_cost = cost / tasks
    lines = [
        "This section is an extrapolation, not a measurement.",
        "Formula: projected sequential = tasks * (measured sequential time / measured task count). "
        f"Projected wall = ceil(tasks / {cap}) * that same per-task time. "
        f"The cap {cap} is workers.max_workers. Slots run together, so wall grows by batches of {cap}.",
        f"Average cost per task: ${per_cost:.4f}.",
        "",
        "| Tasks | Projected wall | Projected sequential |",
        "|---|---|---|",
    ]
    for count in (5, 10, 20):
        slots = (count + cap - 1) // cap
        lines.append(f"| {count} | {slots * per_task:.1f}s | {count * per_task:.1f}s |")
    return lines


_STAGES = ("scan", "baseline", "planner", "fleet", "review", "revise", "integrate")


def parse_timing(text: str) -> dict[str, float]:
    found: dict[str, float] = {}
    for line in text.splitlines():
        if not line.startswith("- ") or ":" not in line:
            continue
        name, _, rest = line[2:].partition(":")
        found[name.strip()] = float(rest.strip().rstrip("s"))
    return found


def explain_variance(
    left: dict[str, float],
    right: dict[str, float],
    *,
    detail: str = "",
) -> str:
    """Say which stage accounts for the gap. An unexplained gap says so."""
    deltas = {name: float(right.get(name, 0)) - float(left.get(name, 0)) for name in _STAGES}
    biggest = max(deltas, key=lambda name: deltas[name])
    if deltas[biggest] < 30:
        return "The cause of the wall-time gap is not explained by these stages."
    where = f"The largest stage gap is {biggest}: {deltas[biggest]:.0f}s."
    if biggest == "revise":
        task = f" on {detail}" if detail else ""
        return (
            f"{where} E2#2 spent {float(right.get('revise', 0)):.0f}s in a revise round{task}. "
            f"E2#1 spent {float(left.get('revise', 0)):.0f}s there. That revise round is the extra time."
        )
    return f"{where} That stage is where the extra time went."


def variance_lines(left: dict[str, float], right: dict[str, float], *, detail: str = "") -> list[str]:
    lines = ["## Variance", "", "| Stage | E2#1 | E2#2 |", "|---|---|---|"]
    for name in _STAGES:
        lines.append(f"| {name} | {float(left.get(name, 0)):.1f}s | {float(right.get(name, 0)):.1f}s |")
    lines += ["", explain_variance(left, right, detail=detail)]
    return lines


def merge_rows(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace scenarios that were rerun and keep the others in their original order."""
    ids = {str(row.get("scenario")) for row in new}
    out: list[dict[str, Any]] = []
    done: set[str] = set()
    for row in old:
        sid = str(row.get("scenario"))
        if sid in ids:
            if sid not in done:
                out.extend(item for item in new if str(item.get("scenario")) == sid)
                done.add(sid)
            continue
        out.append(row)
    seen = {str(row.get("scenario")) for row in out}
    out.extend(item for item in new if str(item.get("scenario")) not in seen)
    return out


def enrich(rows: list[dict[str, Any]], runs: Path) -> list[dict[str, Any]]:
    """Fill planner model and timing from the run folder. Does not call a model."""
    for row in rows:
        folder = runs / str(row.get("run_id") or "")
        if not row.get("planner_model"):
            path = folder / "report.json"
            if path.is_file():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    data = {}
                costs = data.get("metrics", {}).get("costs", []) if isinstance(data, dict) else []
                for cost in costs:
                    if isinstance(cost, dict) and cost.get("component") == "planner":
                        row["planner_model"] = str(cost.get("model") or "")
        timing = folder / "timing.md"
        if timing.is_file():
            row["timing"] = parse_timing(timing.read_text(encoding="utf-8"))
        events = folder / "events.jsonl"
        if events.is_file() and row.get("scenario") == "E2":
            for line in events.read_text(encoding="utf-8").splitlines():
                if "revise_started" not in line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                task = payload.get("task_id")
                if task:
                    row["variance_detail"] = str(task)
    return rows


def render_markdown(rows: list[dict[str, Any]], cap: int) -> str:
    lines = ["# TreeFleet evaluation", ""]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["scenario"]), []).append(row)
    if "E2" in grouped and len(grouped["E2"]) >= 2:
        walls = [float(item["wall_s"]) for item in grouped["E2"]]
        lines.append(f"E2 wall time min {min(walls):.1f}s, max {max(walls):.1f}s.")
        lines.append("")
    lines += [
        "| Scenario | Run | Guard | Tasks | Wall | Fleet wall | Review | Cost | Planner | Expectation | Isolation |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['scenario']} | {row.get('label', '')} | {row.get('guard', '')} | "
            f"{row.get('task_count', 0)} | {float(row.get('wall_s') or 0):.1f}s | "
            f"{float(row.get('fleet_wall_s') or 0):.1f}s | {float(row.get('review_s') or 0):.1f}s | "
            f"{row.get('cost_text', '')} | {row.get('planner_model', '')} | "
            f"{row.get('expectation', '')} | {row.get('isolation', '')} |"
        )
    e2 = [row for row in rows if row.get("scenario") == "E2" and isinstance(row.get("timing"), dict)]
    if len(e2) >= 2:
        lines += ["", *variance_lines(e2[0]["timing"], e2[1]["timing"], detail=str(e2[1].get("variance_detail") or ""))]
    failed = [row for row in rows if str(row.get("expectation", "")).startswith("fail")]
    if failed:
        lines += ["", "Failures were not retried."]
        for row in failed:
            lines.append(f"- {row.get('label', row['scenario'])}: {row['expectation']}")
    lines += ["", "## Tokens and Jev", ""]
    for row in rows:
        tokens = row.get("tokens") or {}
        missing = set(row.get("unreported") or [])
        parts = []
        for role, count in sorted(tokens.items()):
            parts.append(f"{role} usage not reported" if role in missing else f"{role} {count}")
        shown = ", ".join(parts) or "none"
        lines.append(f"- {row.get('label', row['scenario'])}: {shown}; Jev calls {row.get('jev_calls', 0)}")
    if any("worker" in (row.get("unreported") or []) for row in rows):
        lines.append("")
        lines.append(
            "Worker usage not reported: CrewAI token_usage and the LiteLLM callback were empty. "
            "No worker token count was invented."
        )
    dirty = [str(row.get("label", "")) for row in rows if row.get("checkout")]
    lines.append("")
    if dirty:
        lines.append("Main checkout was dirty after: " + ", ".join(dirty) + ".")
    else:
        lines.append("Main checkout was clean after every scenario.")
    lines += ["", "## Estimates", ""]
    lines.extend(estimates(rows, cap))
    lines.append("")
    return "\n".join(lines) + "\n"


def render_json(rows: list[dict[str, Any]], cap: int) -> str:
    body = {"scenarios": rows, "concurrency_cap": cap, "estimates": estimates(rows, cap)}
    return json.dumps(body, indent=2) + "\n"
