"""Run scenarios and score them. Offline checks do not call a model."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from treefleet.contracts import FinalReport
from treefleet.evals.scenarios import Scenario, load_scenarios
from treefleet.orchestrator.pipeline import cleanup_run


def load_env(root: Path) -> None:
    env = root / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def reset_demo(repo: Path) -> None:
    """Put the demo back on demo-base. Branches created by runs are kept."""
    for args in (["checkout", "demo-base"], ["reset", "--hard", "demo-base"]):
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            shell=False,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "git reset failed")


def porcelain(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        shell=False,
        check=False,
    )
    return proc.stdout.strip()


def _covered(actual: list[str], expected: list[str], cover: bool) -> bool:
    if not expected:
        return True
    if cover:
        return set(actual) == set(expected)
    return bool(actual) and all(item in expected for item in actual)


def score(scenario: Scenario, report: FinalReport | None, message: str) -> dict[str, Any]:
    """Compare one run with the scenario expectation. A miss is a failure, not a retry."""
    expect = scenario.expect
    reasons: list[str] = []
    guard = "allow"
    head = message[:500].lower()
    if "**input blocked**" in head or "input blocked" in head:
        guard = "block"
    if "**plan rejected**" in head or "plan rejected" in head:
        guard = "plan_rejected"
    if guard != expect.guard:
        reasons.append(f"guard {guard} expected {expect.guard}")
    task_count = len(report.results) if report is not None else 0
    if not (expect.task_min <= task_count <= expect.task_max):
        reasons.append(f"tasks {task_count} not in {expect.task_min}..{expect.task_max}")
    statuses = [item.status for item in report.results] if report is not None else []
    verdicts = [item.verdict for item in report.verdicts] if report is not None else []
    if not _covered(statuses, expect.statuses, expect.cover):
        reasons.append(f"statuses {statuses}")
    if not _covered(verdicts, expect.verdicts, expect.cover):
        reasons.append(f"verdicts {verdicts}")
    isolation = ""
    violation = False
    if report is not None:
        from treefleet.contracts import isolation_overall

        isolation = isolation_overall(report.isolation, report.results)
        if expect.isolation and not isolation.startswith(expect.isolation):
            reasons.append(f"isolation {isolation}")
        violation = not report.isolation.ok and not isolation.startswith("PASS")
    checks = "skip"
    if expect.checks != "skip":
        if report is None or report.integration is None:
            reasons.append("checks missing")
        else:
            results = report.integration.checks
            checks = "pass" if results and all(item.passed for item in results) else "fail"
            if checks != expect.checks:
                reasons.append(f"checks {checks}")
    metrics = report.metrics if report is not None else None
    cost = metrics.total_usd if metrics is not None else 0.0
    cost_text = f"${cost:.4f}" if metrics is not None else "n/a"
    if metrics is not None and any(item.label == "rate unknown" for item in metrics.costs):
        tokens = sum(item.usage.input_tokens + item.usage.output_tokens for item in metrics.costs)
        cost_text = f"{tokens} tokens"
    return {
        "scenario": scenario.id,
        "guard": guard,
        "task_count": task_count,
        "wall_s": metrics.wall_seconds if metrics is not None else 0.0,
        "fleet_wall_s": metrics.fleet_wall_seconds if metrics is not None else 0.0,
        "review_s": metrics.review_seconds if metrics is not None else 0.0,
        "sequential_s": metrics.sequential_seconds if metrics is not None else 0.0,
        "cost_usd": cost,
        "cost_text": cost_text,
        "expectation": "pass" if not reasons else "fail: " + "; ".join(reasons),
        "isolation": isolation,
        "isolation_violation": violation,
        "statuses": statuses,
        "verdicts": verdicts,
        "planner_model": _planner_model(report),
        "tokens": _tokens(report),
        "unreported": [item.component for item in metrics.costs if item.label == "usage not reported"]
        if metrics is not None
        else [],
        "jev_calls": metrics.jev_calls if metrics is not None else 0,
        "message": message[:300],
    }


def _planner_model(report: FinalReport | None) -> str:
    if report is None:
        return ""
    for item in report.metrics.costs:
        if item.component == "planner":
            return item.model
    return ""


def _tokens(report: FinalReport | None) -> dict[str, int]:
    if report is None:
        return {}
    totals: dict[str, int] = {}
    for item in report.metrics.costs:
        totals[item.component] = totals.get(item.component, 0) + item.usage.input_tokens + item.usage.output_tokens
    return totals


def harness_status(rows: list[dict[str, Any]]) -> int:
    """Non-zero when a run left an isolation violation. Expectation misses stay in the report."""
    return 1 if any(row.get("isolation_violation") for row in rows) else 0


def read_report(folder: Path) -> tuple[FinalReport | None, str]:
    path = folder / "report.json"
    text = (folder / "report.md").read_text(encoding="utf-8") if (folder / "report.md").is_file() else ""
    if not path.is_file():
        return None, text
    try:
        return FinalReport.model_validate_json(path.read_text(encoding="utf-8")), text
    except Exception:
        return None, text


def _newest(runs: Path, known: set[str]) -> Path | None:
    if not runs.is_dir():
        return None
    created = [path for path in runs.iterdir() if path.is_dir() and path.name not in known]
    return max(created, key=lambda path: path.stat().st_mtime) if created else None


def _jev_calls(folder: Path) -> int:
    """Count Jev uses when a blocked run has no metrics. One guard decision is one call."""
    path = folder / "events.jsonl"
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if "deterministic+jev" in line)


def _failed_row(scenario: Scenario, label: str, index: int, reason: str) -> dict[str, Any]:
    return {
        "scenario": scenario.id,
        "label": f"{label}#{index}",
        "guard": "error",
        "task_count": 0,
        "wall_s": 0.0,
        "fleet_wall_s": 0.0,
        "review_s": 0.0,
        "sequential_s": 0.0,
        "cost_usd": 0.0,
        "cost_text": "n/a",
        "expectation": f"fail: {reason[:200]}",
        "isolation": "",
        "isolation_violation": False,
        "statuses": [],
        "verdicts": [],
        "tokens": {},
        "jev_calls": 0,
        "message": reason[:300],
        "run_id": "",
        "checkout": "",
    }


def run_live(
    root: Path,
    repo: Path,
    scenarios: list[Scenario] | None = None,
    on_row: Any = None,
) -> list[dict[str, Any]]:
    """Each scenario once, E2 as many times as its repeat says. No retries."""
    from treefleet.cli import main

    load_env(root)
    os.environ.pop("TREEFLEET_MOCK_LLM", None)
    chosen = scenarios if scenarios is not None else load_scenarios(root / "eval" / "scenarios.toml")
    rows: list[dict[str, Any]] = []
    os.chdir(root)
    runs = root / "runs"
    counts: dict[str, int] = {}
    for scenario in chosen:
        for index, (label, flags) in enumerate(scenario.runs(), start=1):
            print(f"eval {label}", flush=True)
            if scenario.force_plan:
                os.environ["TREEFLEET_FORCE_PLAN"] = scenario.force_plan
            else:
                os.environ.pop("TREEFLEET_FORCE_PLAN", None)
            try:
                reset_demo(repo)
            except RuntimeError as exc:
                rows.append(_failed_row(scenario, label, index, str(exc)))
                os.environ.pop("TREEFLEET_FORCE_PLAN", None)
                continue
            known = {path.name for path in runs.iterdir() if path.is_dir()} if runs.is_dir() else set()
            code = 1
            note = ""
            try:
                code = main([scenario.goal, "--repo", str(repo), *flags])
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
                note = exc.code if isinstance(exc.code, str) else ""
            except Exception as exc:
                note = type(exc).__name__
            newest = _newest(runs, known)
            report, text = read_report(newest) if newest is not None else (None, "")
            message = text or note or (f"exit {code}" if code else "")
            row = score(scenario, report, message)
            if scenario.force_plan:
                row["planner_model"] = "forced"
            if report is None and newest is not None:
                row["jev_calls"] = _jev_calls(newest)
            counts[label] = counts.get(label, 0) + 1
            row["label"] = f"{label}#{counts[label]}"
            row["run_id"] = newest.name if newest is not None else ""
            row["checkout"] = porcelain(repo)
            if newest is not None:
                cleanup_run(repo, newest.name)
                row["checkout"] = porcelain(repo)
            rows.append(row)
            os.environ.pop("TREEFLEET_FORCE_PLAN", None)
            print(f"eval {row['label']} {row['expectation']}", flush=True)
            if on_row is not None:
                on_row(rows)
    return rows
