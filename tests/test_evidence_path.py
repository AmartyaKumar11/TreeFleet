"""Unit checks for the evidence write helper."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import _evidence as ev


@pytest.mark.phase11
def test_evidence_path_defaults_to_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TREEFLEET_WRITE_EVIDENCE", raising=False)
    ev.configure_evidence_root(tmp_path)
    path = ev.evidence_path("phase-8/ui-v2/board-1440.png")
    assert path == tmp_path / "phase-8" / "ui-v2" / "board-1440.png"
    assert path.parent.is_dir()
    assert path.is_relative_to(tmp_path)


@pytest.mark.phase11
def test_evidence_path_env_writes_under_fakeroot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """TREEFLEET_WRITE_EVIDENCE=1 targets ROOT/evidence without touching the real checkout."""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(ev, "ROOT", repo)
    monkeypatch.setenv("TREEFLEET_WRITE_EVIDENCE", "1")
    path = ev.evidence_path("phase-99/probe.png")
    assert path == repo / "evidence" / "phase-99" / "probe.png"
    assert path.parent.is_dir()
    path.write_bytes(b"x")
    assert path.is_file()
    assert not (Path(__file__).resolve().parents[1] / "evidence" / "phase-99" / "probe.png").exists()
