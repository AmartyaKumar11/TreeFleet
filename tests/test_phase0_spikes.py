"""Phase 0 gate: the spike summary and pinned package versions."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.phase0

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "docs" / "spikes" / "summary.md"
SPIKES = ("S1", "S2", "S3", "S4", "S5", "S7", "S8", "S9", "S10", "S10b", "S11")


def test_spike_summary_has_result_lines() -> None:
    assert SUMMARY.is_file()
    lines = SUMMARY.read_text(encoding="utf-8").splitlines()
    for sid in SPIKES:
        assert any(line.startswith(f"- {sid} Result:") for line in lines), sid
    s6 = next(line for line in lines if line.startswith("- S6 "))
    assert "DEFERRED" in s6


def test_package_versions_are_pinned() -> None:
    lock = ROOT / "uv.lock"
    compiled = (ROOT / "requirements-orchestrator.txt", ROOT / "requirements-worker.txt")
    if lock.is_file():
        assert lock.stat().st_size > 0
        return
    missing = [path.name for path in compiled if not path.is_file() or path.stat().st_size == 0]
    assert not missing, "uv.lock is absent and compiled requirements are missing: " + ", ".join(missing)
