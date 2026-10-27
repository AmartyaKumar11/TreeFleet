"""Render eval/report.md and eval/report.json. No agent framework."""

from __future__ import annotations

import json
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
        "| Scenario | Run | Guard | Tasks | Wall | Fleet wall | Review | Cost | Expectation | Isolation |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['scenario']} | {row.get('label', '')} | {row.get('guard', '')} | "
            f"{row.get('task_count', 0)} | {float(row.get('wall_s') or 0):.1f}s | "
            f"{float(row.get('fleet_wall_s') or 0):.1f}s | {float(row.get('review_s') or 0):.1f}s | "
            f"{row.get('cost_text', '')} | {row.get('expectation', '')} | {row.get('isolation', '')} |"
        )
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
