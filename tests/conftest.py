"""Session fixtures. Keep tests off the repo-root treefleet.toml."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from treefleet.onboarding import default_toml_text

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def hermetic_treefleet_config(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """TREEFLEET_CONFIG → tmp template (no [pr]), absolute venvs → real checkout envs."""
    folder = tmp_path_factory.mktemp("tf-config")
    wt = folder / "wt"
    wt.mkdir()
    path = folder / "treefleet.toml"
    orch = str((ROOT / ".venv-orch").resolve())
    worker = str((ROOT / ".venv-worker").resolve())
    root = str(wt.resolve())
    windows = root if sys.platform == "win32" else r"C:\wt"
    other = root if sys.platform != "win32" else "/tmp/tf-wt"
    path.write_text(
        default_toml_text(windows=windows, other=other, orch=orch, worker=worker),
        encoding="utf-8",
    )
    previous = os.environ.get("TREEFLEET_CONFIG")
    os.environ["TREEFLEET_CONFIG"] = str(path)
    yield path
    if previous is None:
        os.environ.pop("TREEFLEET_CONFIG", None)
    else:
        os.environ["TREEFLEET_CONFIG"] = previous
