"""Detected test commands and the environment that runs them. No agent framework."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
from pathlib import Path

_FORBIDDEN = ("inspect", "review", "explore", "investigate", "run", "check")
_SCRIPT_NAMES = ("test", "lint", "typecheck")
_KEEP = ("PATH", "PATHEXT", "SystemRoot", "SYSTEMROOT", "TEMP", "TMP", "SYSTEMDRIVE", "COMSPEC")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}$")
_DROP = {"python", "m", "q", "run"}


def command_argv(command: str) -> list[str]:
    """Split a check command. On Windows, npm is a cmd that CreateProcess cannot start."""
    argv = shlex.split(command, posix=os.name != "nt")
    if os.name != "nt" or not argv:
        return argv
    name = Path(argv[0]).name.lower()
    if name not in {"npm", "npm.cmd", "npx", "npx.cmd"}:
        return argv
    node = shutil.which("node")
    if not node:
        return argv
    script_name = "npx-cli.js" if name.startswith("npx") else "npm-cli.js"
    script = Path(node).resolve().parent / "node_modules" / "npm" / "bin" / script_name
    if not script.is_file():
        return argv
    return [node, str(script), *argv[1:]]


def check_env() -> dict[str, str]:
    """PATH so python and node resolve. No API keys and no other parent variables."""
    env: dict[str, str] = {}
    for key in _KEEP:
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def forbidden_stem(text: str) -> str | None:
    """The first word, when it is a read-only or command task."""
    stripped = text.strip()
    if not stripped:
        return None
    word = stripped.split(None, 1)[0].lower().strip(".,:;\"'")
    for stem in _FORBIDDEN:
        if word == stem:
            return stem
    return None


def detect_checks(repo: Path) -> dict[str, str]:
    """Commands from CI run steps, package.json scripts, and pytest or ruff config."""
    found: dict[str, str] = {}
    taken: set[str] = set()
    for command in _ci_runs(repo / ".github" / "workflows" / "ci.yml"):
        _add(found, taken, _name_for(command), command)
    package = repo / "package.json"
    if package.is_file():
        try:
            data = json.loads(package.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = None
        scripts = data.get("scripts") if isinstance(data, dict) else None
        if isinstance(scripts, dict):
            for name in _SCRIPT_NAMES:
                command = scripts.get(name)
                if isinstance(command, str) and command.strip():
                    _add(found, taken, name, command.strip())
    if _has_pytest(repo):
        _add(found, taken, "pytest", "python -m pytest -q")
    if _has_ruff(repo):
        _add(found, taken, "ruff", "ruff check")
    return found


def _add(found: dict[str, str], taken: set[str], name: str, command: str) -> None:
    command = command.strip()
    if not command or len(command) > 300 or command in found.values():
        return
    slug = _unique(name, taken)
    if slug is None:
        return
    found[slug] = command


def _unique(name: str, taken: set[str]) -> str | None:
    base = name[:39]
    if not _SLUG.fullmatch(base):
        return None
    candidate = base
    number = 2
    while candidate in taken:
        suffix = f"-{number}"
        candidate = base[: 39 - len(suffix)] + suffix
        number += 1
    taken.add(candidate)
    return candidate


def _name_for(command: str) -> str:
    tokens = [token for token in re.findall(r"[a-z0-9]+", command.lower()) if token not in _DROP]
    name = "-".join(tokens[:5]) or "check"
    if len(name) < 2:
        name = "check"
    return name[:39]


def _ci_runs(path: Path) -> list[str]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    found: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        marker = "- run:" if stripped.startswith("- run:") else "run:" if stripped.startswith("run:") else ""
        if not marker:
            index += 1
            continue
        rest = stripped.split(":", 1)[1].strip().strip("'\"")
        if rest in {"|", ">", "|+", ">-", "|-", ">+"}:
            indent = len(line) - len(line.lstrip())
            index += 1
            while index < len(lines):
                block = lines[index]
                if block.strip() and len(block) - len(block.lstrip()) <= indent:
                    break
                if block.strip():
                    found.append(block.strip().strip("'\""))
                index += 1
            continue
        if rest:
            found.append(rest)
        index += 1
    return found


def _has_pytest(repo: Path) -> bool:
    if (repo / "pytest.ini").is_file():
        return True
    setup = repo / "setup.cfg"
    if setup.is_file() and "[tool:pytest]" in setup.read_text(encoding="utf-8", errors="replace"):
        return True
    return _pyproject_has(repo, "[tool.pytest")


def _has_ruff(repo: Path) -> bool:
    if (repo / "ruff.toml").is_file() or (repo / ".ruff.toml").is_file():
        return True
    return _pyproject_has(repo, "[tool.ruff")


def _pyproject_has(repo: Path, marker: str) -> bool:
    path = repo / "pyproject.toml"
    if not path.is_file():
        return False
    return marker in path.read_text(encoding="utf-8", errors="replace")
