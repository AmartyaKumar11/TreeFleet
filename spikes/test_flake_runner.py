"""Run test_four_processes_append_without_torn_lines many times; optional CPU load.

Usage:
  python spikes/test_flake_runner.py              # 30 isolated runs
  python spikes/test_flake_runner.py --load 10    # 10 runs under a busy-loop
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST = "tests/test_events_integrity.py::test_four_processes_append_without_torn_lines"


def run_once() -> tuple[bool, str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=line", TEST],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    out = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, out


def busy_loop_worker(seconds: float) -> subprocess.Popen[str]:
    code = (
        "import time\n"
        f"end=time.time()+{seconds!r}\n"
        "x=0\n"
        "while time.time()<end:\n"
        " x=(x*1103515245+12345)&0xffffffff\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--load", type=int, default=0, help="extra runs under CPU busy-loop")
    args = parser.parse_args()

    failures: list[tuple[str, int, str]] = []

    print(f"Isolated: {args.runs} runs of {TEST}")
    for i in range(1, args.runs + 1):
        ok, out = run_once()
        print(f"  iso {i:02d}: {'PASS' if ok else 'FAIL'}")
        if not ok:
            failures.append(("iso", i, out))

    if args.load:
        print(f"Under load: {args.load} runs")
        # Keep the busy loop alive across the batch.
        burner = busy_loop_worker(max(60.0, args.load * 25.0))
        try:
            time.sleep(0.5)
            for i in range(1, args.load + 1):
                ok, out = run_once()
                print(f"  load {i:02d}: {'PASS' if ok else 'FAIL'}")
                if not ok:
                    failures.append(("load", i, out))
        finally:
            burner.terminate()
            try:
                burner.wait(timeout=5)
            except subprocess.TimeoutExpired:
                burner.kill()

    total = args.runs + args.load
    failed = len(failures)
    print("=" * 60)
    print(f"total={total} passed={total - failed} failed={failed} rate={failed / max(total, 1):.1%}")
    if failures:
        kind, num, out = failures[0]
        print(f"first failure ({kind} #{num}):")
        lines = out.splitlines()
        for idx, line in enumerate(lines):
            if "AssertionError" in line or "assert " in line or "FAILED" in line:
                print("\n".join(lines[max(0, idx - 4) : idx + 12]))
                break
        else:
            print("\n".join(lines[-40:]))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
