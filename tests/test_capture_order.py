"""The capture records lane order from board snapshots."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.phase8

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "capture_live_real2.py"
_SPEC = importlib.util.spec_from_file_location("capture_live_real2", _PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def test_one_task_must_visit_working_then_review_then_ready() -> None:
    assert _MODULE.saw_lane_order({"alpha": ["working", "review", "ready"]})
    assert _MODULE.saw_lane_order({"alpha": ["needs", "working", "working", "review", "ready", "ready"]})
    assert not _MODULE.saw_lane_order({"alpha": ["needs", "needs"]})
    assert not _MODULE.saw_lane_order({"alpha": ["ready", "review", "working"]})
    history: dict[str, list[str]] = {}
    _MODULE.note_lanes(history, [{"id": "alpha", "lane": "working"}, {"id": "beta", "lane": "needs"}])
    _MODULE.note_lanes(history, [{"id": "alpha", "lane": "working"}, {"id": "beta", "lane": "review"}])
    _MODULE.note_lanes(history, [{"id": "alpha", "lane": "review"}, {"id": "beta", "lane": "ready"}])
    assert history["alpha"] == ["working", "review"]
    assert history["beta"] == ["needs", "review", "ready"]
