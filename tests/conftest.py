"""Session fixtures. Keep tests off the repo-root treefleet.toml."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests._evidence import configure_evidence_root
from treefleet.onboarding import default_toml_text

ROOT = Path(__file__).resolve().parents[1]

_PLAYWRIGHT_INSTALL_MSG = "run: playwright install chromium"
_chromium_ok: bool | None = None


def _chromium_available() -> bool:
    """One session-level Playwright Chromium probe (cached)."""
    global _chromium_ok
    if _chromium_ok is not None:
        return _chromium_ok
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _chromium_ok = False
        return False
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            browser.close()
        _chromium_ok = True
    except Exception:
        _chromium_ok = False
    return _chromium_ok


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "ui: Playwright browser tests; need playwright install chromium")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    ui_items = [item for item in items if item.get_closest_marker("ui") is not None]
    if not ui_items:
        return
    if _chromium_available():
        return
    require = os.environ.get("TREEFLEET_REQUIRE_UI", "").strip() in {"1", "true", "yes"}
    if require:
        pytest.exit(f"UI tests require Chromium ({_PLAYWRIGHT_INSTALL_MSG})", returncode=1)
    skip = pytest.mark.skip(reason=_PLAYWRIGHT_INSTALL_MSG)
    for item in ui_items:
        item.add_marker(skip)


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


@pytest.fixture(scope="session", autouse=True)
def evidence_tmpdir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """Screenshots and evidence writes go here unless TREEFLEET_WRITE_EVIDENCE=1."""
    root = tmp_path_factory.mktemp("evidence-out")
    configure_evidence_root(root)
    yield root
