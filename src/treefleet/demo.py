"""Demo preflight and reset. Prints check names and fix commands, never a secret value."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from treefleet.config import MissingSecretError, load_settings
from treefleet.cursor_models import UnknownModelError, discover
from treefleet.cursor_run import launch_command
from treefleet.git.worktrees import list_worktrees, remove_worktree

_KEYS = ("DEEPSEEK_API_KEY", "TYPESAFE_API_KEY")


def login_ok(text: str, code: int) -> bool:
    folded = text.lower()
    return code == 0 and "logged in" in folded and "not logged" not in folded


def _git(repo: Path, args: list[str], timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
        check=False,
    )


def _branch(repo: Path) -> str:
    return _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()


def _clean(repo: Path) -> bool:
    return not _git(repo, ["status", "--porcelain"]).stdout.strip()


def _stale(repo: Path) -> list[Path]:
    main = repo.resolve()
    found: list[Path] = []
    for item in list_worktrees(repo):
        try:
            if item.path.resolve() == main:
                continue
        except OSError:
            continue
        found.append(item.path)
    return found


def _default_login(cli: Path) -> tuple[bool, str]:
    if shutil.which(str(cli)) is None and not cli.is_file():
        return False, "install cursor-agent and put it on PATH"
    command = [*launch_command(cli), "status"]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=40,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "cursor-agent login"
    if login_ok(proc.stdout + proc.stderr, proc.returncode):
        return True, ""
    return False, "cursor-agent login"


def _default_models(cli: Path) -> tuple[bool, str]:
    try:
        found = discover(cli, "--list-models")
    except (UnknownModelError, OSError, subprocess.TimeoutExpired):
        return False, "cursor-agent --list-models"
    if not found:
        return False, "cursor-agent --list-models"
    return True, ""


def preflight(
    repo: Path,
    *,
    env: Mapping[str, str] | None = None,
    login: Callable[[Path], tuple[bool, str]] | None = None,
    models: Callable[[Path], tuple[bool, str]] | None = None,
) -> list[tuple[bool, str]]:
    """One line per check: ok, and the text to print. The text has no secret value."""
    found = env if env is not None else os.environ
    lines: list[tuple[bool, str]] = []
    for key in _KEYS:
        if found.get(key):
            lines.append((True, f"green  {key} is set"))
        else:
            lines.append((False, f"red    {key} is unset\n       fix: set {key}"))
    cli = Path("cursor-agent")
    try:
        settings = load_settings(Path.cwd() / "treefleet.toml")
        cli = Path(settings.cursor.cli)
    except MissingSecretError:
        pass
    logged, login_fix = (login or _default_login)(cli)
    if logged:
        lines.append((True, "green  cursor-agent is on PATH and logged in"))
    else:
        lines.append((False, f"red    cursor-agent is missing or not logged in\n       fix: {login_fix}"))
    listed, model_fix = (models or _default_models)(cli)
    if listed:
        lines.append((True, "green  cursor model list loads"))
    else:
        lines.append((False, f"red    cursor model list did not load\n       fix: {model_fix}"))
    if not (repo / ".git").exists():
        lines.append((False, f"red    {repo} is not a git repository\n       fix: git init"))
        return lines
    branch = _branch(repo)
    if branch == "demo-base":
        lines.append((True, "green  repo is on demo-base"))
    else:
        lines.append((False, f"red    repo is on {branch}\n       fix: git -C {repo} checkout demo-base"))
    if _clean(repo):
        lines.append((True, "green  working tree is clean"))
    else:
        lines.append((False, f"red    working tree is dirty\n       fix: treefleet demo reset --repo {repo}"))
    stale = _stale(repo)
    if not stale:
        lines.append((True, "green  no stale worktrees"))
    else:
        lines.append((False, f"red    {len(stale)} stale worktree(s)\n       fix: treefleet demo reset --repo {repo}"))
    return lines


def tf_branches(repo: Path) -> list[str]:
    proc = _git(repo, ["for-each-ref", "--format=%(refname:short)", "refs/heads/"])
    return [line.strip() for line in proc.stdout.splitlines() if line.strip().startswith("tf/")]


def reset_repo(repo: Path, *, confirm: Callable[[list[str]], bool]) -> list[str]:
    """Remove extra worktrees, return to demo-base, and delete tf/* branches only after confirm."""
    notes: list[str] = []
    for path in _stale(repo):
        remove_worktree(repo, path)
        notes.append(f"removed worktree {path}")
    checkout = _git(repo, ["checkout", "demo-base"])
    if checkout.returncode != 0:
        raise RuntimeError(checkout.stderr.strip() or "git checkout demo-base failed")
    hard = _git(repo, ["reset", "--hard", "demo-base"])
    if hard.returncode != 0:
        raise RuntimeError(hard.stderr.strip() or "git reset failed")
    cleaned = _git(repo, ["clean", "-fd"])
    if cleaned.returncode != 0:
        raise RuntimeError(cleaned.stderr.strip() or "git clean failed")
    notes.append("demo-base clean")
    branches = tf_branches(repo)
    for name in branches:
        notes.append(f"branch {name}")
    if branches and confirm(branches):
        for name in branches:
            deleted = _git(repo, ["branch", "-D", name])
            if deleted.returncode != 0:
                raise RuntimeError(deleted.stderr.strip() or f"could not delete {name}")
            notes.append(f"deleted {name}")
    elif branches:
        notes.append("kept tf branches")
    return notes


def main(argv: list[str] | None = None) -> int:
    import argparse

    from treefleet.evals.harness import load_env

    load_env(Path.cwd())
    parser = argparse.ArgumentParser(prog="treefleet demo")
    parser.add_argument("command", choices=("preflight", "reset"))
    parser.add_argument("--repo", required=True)
    parsed = parser.parse_args(argv)
    repo = Path(parsed.repo)
    if parsed.command == "preflight":
        rows = preflight(repo)
        for _ok, text in rows:
            print(text)
        return 0 if all(ok for ok, _text in rows) else 1

    def ask(branches: list[str]) -> bool:
        for name in branches:
            print(name)
        if not sys.stdin.isatty():
            print("not a terminal, branches kept")
            return False
        try:
            answer = input("Delete these branches? [y/N] ")
        except EOFError:
            print("not a terminal, branches kept")
            return False
        return answer.strip().lower() in {"y", "yes"}

    for note in reset_repo(repo, confirm=ask):
        print(note)
    return 0
