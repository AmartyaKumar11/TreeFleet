#!/usr/bin/env python3
"""Cumulative phase gate: lint, types and tests for phases 0..N. Exit code 0 only if every step passes."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_PHASE = 11


def _venv_python(name: str) -> Path:
    if sys.platform == "win32":
        return ROOT / name / "Scripts" / "python.exe"
    return ROOT / name / "bin" / "python"


def _orch_targets() -> list[str]:
    """src, tests and scripts, without the worker package, which has its own environment."""
    worker = ROOT / "src" / "treefleet" / "worker"
    sample_templates = ROOT / "src" / "treefleet" / "sample" / "templates"
    if not worker.exists():
        return ["src", "tests", "scripts"]
    targets = ["tests", "scripts"]
    for path in (ROOT / "src").rglob("*.py"):
        if worker in path.parents or sample_templates in path.parents:
            continue
        targets.append(path.relative_to(ROOT).as_posix())
    return targets


def _pyright_steps(py: str) -> list[tuple[str, list[str]]]:
    orch = _venv_python(".venv-orch")
    steps = [("pyright orch", [py, "-m", "pyright", "--pythonpath", str(orch), *_orch_targets()])]
    worker = ROOT / "src" / "treefleet" / "worker"
    if worker.exists():
        worker_py = _venv_python(".venv-worker")
        steps.append(("pyright worker", [py, "-m", "pyright", "--pythonpath", str(worker_py), "src/treefleet/worker"]))
    return steps


def run(label: str, cmd: list[str]) -> bool:
    print(f"\n=== {label}: {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, cwd=ROOT).returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the TreeFleet gate for a phase and every earlier phase.")
    parser.add_argument("--phase", type=int, required=True, choices=range(MAX_PHASE + 1))
    parser.add_argument("--step", type=int, default=None, help="phase step being gated; reported in the result line")
    parser.add_argument("--live", action="store_true", help="include tests marked live (real model and network calls)")
    parser.add_argument("--skip", action="append", default=[], choices=["ruff", "pyright"], help="skip a tool")
    args = parser.parse_args()

    markers = " or ".join(f"phase{i}" for i in range(args.phase + 1))
    expression = f"({markers})" + ("" if args.live else " and not live")

    py = sys.executable
    steps: list[tuple[str, list[str]]] = []
    if "ruff" not in args.skip:
        steps.append(("ruff lint", [py, "-m", "ruff", "check", "."]))
        steps.append(("ruff format", [py, "-m", "ruff", "format", "--check", "."]))
    if "pyright" not in args.skip:
        steps.extend(_pyright_steps(py))
    # Missing Playwright browsers must FAIL in the gate, never skip UI tests green.
    pytest_env = {**os.environ, "TREEFLEET_REQUIRE_UI": "1"}
    steps.append(("pytest", [py, "-m", "pytest", "-q", "-m", expression]))

    failed: list[str] = []
    for label, cmd in steps:
        if label == "pytest":
            print(f"\n=== {label}: {' '.join(cmd)}", flush=True)
            if subprocess.run(cmd, cwd=ROOT, env=pytest_env).returncode != 0:
                failed.append(label)
        elif not run(label, cmd):
            failed.append(label)
    step = f" step {args.step}" if args.step is not None else ""
    print("\n" + ("GATE GREEN" if not failed else f"GATE RED: {', '.join(failed)}") + f" (phase {args.phase}{step})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
