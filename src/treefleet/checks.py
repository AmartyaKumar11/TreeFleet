"""Detected test commands and the environment that runs them. No agent framework."""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from treefleet.git.worktrees import create_worktree, remove_worktree, run_git

_FORBIDDEN = ("inspect", "review", "explore", "investigate", "run", "check")
_SCRIPT_NAMES = ("test", "lint", "typecheck")
_SETUP = ("pip install", "npm ci", "npm install", "uv sync")
_KEEP = ("PATH", "PATHEXT", "SystemRoot", "SYSTEMROOT", "TEMP", "TMP", "SYSTEMDRIVE", "COMSPEC")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}$")
_DROP = {"python", "m", "q", "run"}


def _split(command: str) -> list[str]:
    """Split a command. On Windows a backslash is a path separator, not an escape."""
    if os.name != "nt":
        return shlex.split(command)
    return shlex.split(command.replace("\\", "\\\\"), posix=True)


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    return text


def command_argv(command: str) -> list[str]:
    """Split a check command. On Windows, npm is a cmd that CreateProcess cannot start."""
    argv = _split(command)
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


def check_env(path_prefix: str | None = None) -> dict[str, str]:
    """PATH so python and node resolve. No API keys and no other parent variables."""
    env: dict[str, str] = {}
    for key in _KEEP:
        value = os.environ.get(key)
        if value:
            env[key] = value
    prefix = path_prefix or os.environ.get("TREEFLEET_PYTHON_DIR")
    if prefix:
        current = env.get("PATH", "")
        env["PATH"] = prefix + (os.pathsep + current if current else "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def is_setup(command: str) -> bool:
    """Install and sync commands are not checks and are never executed."""
    text = command.lower()
    return any(token in text for token in _SETUP)


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
        rest = _unquote(stripped.split(":", 1)[1])
        if rest in {"|", ">", "|+", ">-", "|-", ">+"}:
            indent = len(line) - len(line.lstrip())
            index += 1
            while index < len(lines):
                block = lines[index]
                if block.strip() and len(block) - len(block.lstrip()) <= indent:
                    break
                if block.strip():
                    found.append(_unquote(block))
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


@dataclass(frozen=True)
class SelectedChecks:
    checks: dict[str, str]
    notes: list[str]
    python_path: str = ""
    python_source: str = ""


def resolve_python(
    repo: Path,
    explicit: Path | None = None,
    *,
    imports: Any = None,
    path_exes: list[Path] | None = None,
) -> tuple[Path | None, str]:
    """First interpreter that can import pytest: flag, repo venv, PATH, then this process."""
    can_import = imports_pytest if imports is None else imports
    candidates: list[tuple[Path, str]] = []
    if explicit is not None:
        candidates.append((Path(explicit), "--python"))
    for name in (".venv", "venv"):
        exe = _venv_python(repo / name)
        if exe is not None:
            candidates.append((exe, name))
    for exe in path_pythons() if path_exes is None else path_exes:
        candidates.append((Path(exe), "PATH"))
    candidates.append((Path(sys.executable), "orchestrator"))
    seen: set[str] = set()
    for exe, source in candidates:
        key = os.path.normcase(str(exe))
        if key in seen:
            continue
        seen.add(key)
        if can_import(exe):
            return exe, source
    return None, ""


def rewrite_python(command: str, python: Path) -> str:
    """Replace a bare python token. An already-qualified path stays as written."""
    try:
        argv = _split(command)
    except ValueError:
        return command
    if not argv or _qualified(argv[0]) or Path(argv[0]).name.lower() not in {"python", "python.exe"}:
        return command
    # Keep the rest of the text. Rejoining drops quotes that python -c needs.
    stripped = command.lstrip()
    return command[: len(command) - len(stripped)] + str(python) + stripped[len(argv[0]) :]


def python_prefix(checks: dict[str, str]) -> str | None:
    """Directory of a resolved interpreter already written into a check command."""
    for command in checks.values():
        try:
            argv = _split(command)
        except ValueError:
            continue
        if not argv:
            continue
        exe = Path(argv[0])
        if exe.is_file() and exe.name.lower() in {"python", "python.exe", "python3", "python3.exe"}:
            return str(exe.parent)
    return None


def select_checks(
    repo: Path,
    detected: dict[str, str],
    *,
    worktree_root: Path,
    explicit: Path | None = None,
    run_id: str = "probe01",
) -> SelectedChecks:
    """Drop setup commands, rewrite python, and keep checks that pass on the clean base."""
    notes: list[str] = []
    runnable: dict[str, str] = {}
    for name, command in detected.items():
        if is_setup(command):
            notes.append(f"{name} is setup ({command}) and was not run")
            continue
        runnable[name] = command
    python_path = ""
    python_source = ""
    if explicit is not None or any(_uses_python(command) for command in runnable.values()):
        resolved, source = resolve_python(repo, explicit)
        if resolved is None:
            kept: dict[str, str] = {}
            for name, command in runnable.items():
                if _uses_python(command):
                    notes.append(f"warning: no interpreter can import pytest; dropped {name}")
                else:
                    kept[name] = command
            runnable = kept
        else:
            python_path = str(resolved)
            python_source = source
            runnable = {name: rewrite_python(command, resolved) for name, command in runnable.items()}
    if not runnable:
        if detected:
            notes.append("warning: no checks passed on the base commit")
        return SelectedChecks({}, _clip(notes), python_path, python_source)
    kept_checks, dropped = _probe(repo, runnable, worktree_root, run_id, python_path)
    notes.extend(dropped)
    if not kept_checks:
        notes.append("warning: no checks passed on the base commit")
    return SelectedChecks(kept_checks, _clip(notes), python_path, python_source)


def run_command(command: str, cwd: Path, path_prefix: str | None = None) -> tuple[int, str, str]:
    """One check. shell stays false. A missing program is exit 127."""
    try:
        argv = command_argv(command)
    except ValueError as exc:
        return 127, "", str(exc)
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
            check=False,
            env=check_env(path_prefix),
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _uses_python(command: str) -> bool:
    if "pytest" in command.lower():
        return True
    try:
        argv = _split(command)
    except ValueError:
        return False
    return bool(argv) and not _qualified(argv[0]) and Path(argv[0]).name.lower() in {"python", "python.exe"}


def _qualified(token: str) -> bool:
    return "/" in token or "\\" in token


def _venv_python(folder: Path) -> Path | None:
    for relative in ("Scripts/python.exe", "bin/python"):
        candidate = folder.joinpath(*relative.split("/"))
        if candidate.is_file():
            return candidate
    return None


def path_pythons() -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    names = ("python.exe", "python") if os.name == "nt" else ("python3", "python")
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if not folder:
            continue
        for name in names:
            candidate = Path(folder) / name
            if not candidate.is_file():
                continue
            key = os.path.normcase(str(candidate))
            if key not in seen:
                seen.add(key)
                found.append(candidate)
            break
    return found


def imports_pytest(exe: Path) -> bool:
    if not exe.is_file():
        return False
    try:
        proc = subprocess.run(
            [str(exe), "-c", "import pytest"],
            capture_output=True,
            timeout=30,
            shell=False,
            check=False,
            env=check_env(),
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def _probe(
    repo: Path,
    checks: dict[str, str],
    root: Path,
    run_id: str,
    python_path: str,
) -> tuple[dict[str, str], list[str]]:
    task_id = "probe"
    path = create_worktree(repo, run_id, task_id, root, "HEAD")
    prefix = str(Path(python_path).parent) if python_path else None
    kept: dict[str, str] = {}
    notes: list[str] = []
    try:
        for name, command in checks.items():
            code, out, err = run_command(command, path, prefix)
            if code == 0:
                kept[name] = command
                continue
            notes.append(f"dropped {name}: {_reason(err, out, code)}")
    finally:
        remove_worktree(repo, path)
        run_git(["branch", "-D", f"tf/{run_id}/{task_id}"], cwd=repo)
    return kept, notes


def _reason(err: str, out: str, code: int) -> str:
    text = err.strip() or out.strip()
    line = text.splitlines()[-1].strip() if text else f"exit {code}"
    return line[:180]


def _clip(notes: list[str]) -> list[str]:
    return [note[:300] for note in notes[:20]]
