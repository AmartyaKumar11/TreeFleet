"""Pre-run confirmation screen. Thin printer over existing settings."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from treefleet.config import Settings

Ask = Callable[[str], str]

SAFE_SENTENCE = (
    "TreeFleet works only in its own worktrees and branches tf/<run>/<task>; it will not modify your checkout."
)


def _git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        shell=False,
        check=False,
    )


def remote_slug(repo: Path) -> str | None:
    proc = _git(repo, ["remote", "get-url", "origin"])
    if proc.returncode != 0:
        return None
    raw = proc.stdout.strip()
    if not raw:
        return None
    text = raw[:-4] if raw.endswith(".git") else raw
    if text.startswith("git@"):
        _, _, rest = text.partition(":")
        parts = [p for p in rest.split("/") if p]
    else:
        parts = [p for p in text.replace("\\", "/").split("/") if p]
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return None


def base_branch(repo: Path) -> str:
    symbolic = _git(repo, ["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"])
    if symbolic.returncode == 0:
        ref = symbolic.stdout.strip()
        if ref.startswith("refs/remotes/origin/"):
            return ref.removeprefix("refs/remotes/origin/")
    current = _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    name = current.stdout.strip()
    return name if current.returncode == 0 and name and name != "HEAD" else "(unknown)"


def working_tree_state(repo: Path) -> str:
    dirty = bool(_git(repo, ["status", "--porcelain"]).stdout.strip())
    return "dirty (warning)" if dirty else "clean"


def confirm_lines(repo: Path, settings: Settings) -> list[str]:
    """Facts only. No secrets."""
    slug = remote_slug(repo)
    repo_line = f"repo: {repo.resolve()}"
    if slug:
        repo_line += f" (remote {slug})"
    pr = settings.pr
    if pr.enabled:
        pr_line = f"PR mode: on → {pr.repo} (base {pr.base})"
    else:
        pr_line = "PR mode: off"
    router = settings.models.router
    lines = [
        "=== TreeFleet run confirmation ===",
        repo_line,
        f"base branch: {base_branch(repo)}",
        f"working tree: {working_tree_state(repo)}",
        pr_line,
        f"worktree root: {settings.worktree_root}",
        f"agent CLI: {settings.cursor.cli}",
        (
            f"model tiers: default_worker={settings.models.default_worker}; "
            f"router simple={router.simple} moderate={router.moderate} complex={router.complex}"
        ),
        (
            f"budgets: max_tasks={settings.guards.max_tasks} "
            f"max_workers={settings.workers.max_workers} "
            f"revise_rounds={settings.revise.max_rounds}"
        ),
        SAFE_SENTENCE,
    ]
    return lines


def confirm_run(
    repo: Path,
    settings: Settings,
    *,
    yes: bool = False,
    skip_prompt: bool = False,
    ask: Ask | None = None,
    interactive: bool | None = None,
) -> tuple[int, list[str]]:
    """Print confirmation. Exit 0 proceed, 1 declined, 2 non-interactive without --yes."""
    lines = confirm_lines(repo, settings)
    tty = sys.stdin.isatty() if interactive is None else interactive
    if yes or skip_prompt:
        lines.append("proceeding (--yes)")
        return 0, lines
    if not tty:
        lines.append("refusing: non-interactive session without --yes")
        return 2, lines
    ask_fn = ask or (lambda prompt: input(prompt))
    try:
        answer = ask_fn("Start this run? [y/N] ")
    except EOFError:
        lines.append("refusing: no input for confirmation")
        return 2, lines
    if answer.strip().lower() in {"y", "yes"}:
        lines.append("confirmed")
        return 0, lines
    lines.append("declined")
    return 1, lines
