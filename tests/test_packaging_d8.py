"""15.D8: wheel must ship ui/static assets for non-editable installs."""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.packaging

ROOT = Path(__file__).resolve().parents[1]


def _build_wheel(outdir: Path) -> None:
    direct = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(outdir)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if direct.returncode == 0:
        return
    if shutil.which("uv"):
        via_uv = subprocess.run(
            [
                "uv",
                "run",
                "--with",
                "build",
                "--with",
                "wheel",
                "--with",
                "setuptools",
                "python",
                "-m",
                "build",
                "--wheel",
                "--outdir",
                str(outdir),
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        assert via_uv.returncode == 0, via_uv.stderr or via_uv.stdout or direct.stderr
        return
    raise AssertionError(direct.stderr or direct.stdout or "python -m build failed and uv is unavailable")


def test_wheel_includes_ui_static(tmp_path: Path) -> None:
    out = tmp_path / "dist"
    out.mkdir()
    _build_wheel(out)
    wheels = list(out.glob("treefleet-*.whl"))
    assert len(wheels) == 1, wheels
    with zipfile.ZipFile(wheels[0]) as zf:
        names = set(zf.namelist())
    assert "treefleet/ui/static/js/board.js" in names
    assert "treefleet/ui/static/js/transcript.js" in names
    assert "treefleet/ui/static/css/tokens.css" in names
    fonts = [n for n in names if n.startswith("treefleet/ui/static/fonts/") and not n.endswith("/")]
    assert fonts, "expected at least one font under treefleet/ui/static/fonts/"
