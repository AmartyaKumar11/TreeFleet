"""Where Playwright and other tests may write evidence artifacts.

Default: a pytest temp directory (see conftest). Set TREEFLEET_WRITE_EVIDENCE=1
to write under the real repo ``evidence/`` tree instead.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_ENV = "TREEFLEET_WRITE_EVIDENCE"
_tmp_root: Path | None = None


def configure_evidence_root(path: Path) -> None:
    """Point default (non-env) writes at ``path``."""
    global _tmp_root
    _tmp_root = path


def evidence_path(relative: str | Path) -> Path:
    """Resolve ``evidence/<relative>`` under temp or the real tree; mkdir parents."""
    rel = Path(relative)
    if os.environ.get(_ENV) == "1":
        base = ROOT / "evidence"
    else:
        if _tmp_root is None:
            msg = "evidence root not configured; rely on the session fixture or TREEFLEET_WRITE_EVIDENCE=1"
            raise RuntimeError(msg)
        base = _tmp_root
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
