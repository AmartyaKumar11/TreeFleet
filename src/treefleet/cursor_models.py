"""Discover Cursor model ids and reject any id the CLI does not list."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from treefleet.contracts import ModelChoice

_CACHE: dict[str, tuple[CursorModel, ...]] = {}
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff"), None)
_MARKER = re.compile(r"\s*\((?:current|default)(?:\s*,\s*(?:current|default))?\)\s*$", re.IGNORECASE)


class UnknownModelError(RuntimeError):
    """The id is not in the CLI's model list. The message names the id and no secret."""


@dataclass(frozen=True)
class CursorModel:
    id: str
    label: str


def parse_model_list(text: str) -> tuple[CursorModel, ...]:
    """Parse `cursor-agent --list-models`. Header, blanks and the Tip paragraph are not models."""
    found: list[CursorModel] = []
    for raw in text.splitlines():
        line = raw.translate(_ZERO_WIDTH).strip()
        if not line or line.casefold() == "available models":
            continue
        if line.casefold().startswith("tip:"):
            break
        if " - " in line:
            model_id, label = line.split(" - ", 1)
            label = _MARKER.sub("", label).strip()
            found.append(CursorModel(model_id.strip(), label))
        else:
            found.append(CursorModel(line, line))
    return tuple(found)


def discover(cli: Path, list_flag: str = "--list-models") -> tuple[CursorModel, ...]:
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
    found = parse_model_list(proc.stdout)
    _CACHE[key] = found
    return found


def _ids(cli: Path, list_flag: str) -> set[str]:
    return {item.id for item in discover(cli, list_flag)}


def require_known(model: ModelChoice, cli: Path, list_flag: str = "--list-models") -> None:
    """`router` is TreeFleet routing. `auto` is Cursor's own model and must be discovered."""
    if "[" in model or "]" in model:
        raise UnknownModelError(
            f"{model} uses a bracketed override, which is not supported in this version. "
            "Run `treefleet models` to see valid ids."
        )
    if model == "router":
        return
    if model not in _ids(cli, list_flag):
        raise UnknownModelError(f"{model} is not a known Cursor model. Run `treefleet models`.")


def require_tiers(model_ids: list[str], cli: Path, list_flag: str = "--list-models") -> None:
    known = _ids(cli, list_flag)
    for model_id in model_ids:
        if not model_id or model_id == "router":
            continue
        if model_id not in known:
            raise UnknownModelError(f"{model_id} is not a known Cursor model. Run `treefleet models`.")
