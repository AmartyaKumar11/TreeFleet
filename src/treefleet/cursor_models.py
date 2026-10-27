"""Discover Cursor model ids and reject any id the CLI does not list."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from treefleet.contracts import ModelChoice

_CACHE: dict[str, tuple[str, ...]] = {}


class UnknownModelError(RuntimeError):
    """The id is not in the CLI's model list. The message names the id and no secret."""


def discover(cli: Path, list_flag: str = "--list-models") -> tuple[str, ...]:
    key = f"{cli.resolve()} {list_flag}"
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    command = [sys.executable, str(cli), list_flag] if cli.suffix.lower() == ".py" else [str(cli), list_flag]
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        shell=False,
        check=False,
    )
    if proc.returncode != 0:
        raise UnknownModelError(f"Could not list Cursor models ({list_flag} exited {proc.returncode}).")
    found = tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())
    _CACHE[key] = found
    return found


def require_known(model: ModelChoice, cli: Path, list_flag: str = "--list-models") -> None:
    """`router` is the TreeFleet sentinel and is not a CLI model id."""
    if model == "router":
        return
    if model not in discover(cli, list_flag):
        raise UnknownModelError(f"{model} is not a known Cursor model. Run `treefleet models`.")
