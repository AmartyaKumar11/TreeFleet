"""Validate and resolve git ref names from untrusted callers."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from treefleet.git.worktrees import git_environ, run_git

_MAX_REF = 200
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


class RefNameError(ValueError):
    """The ref name is not a safe git ref."""


def validate_ref_name(name: str) -> str:
    """Return a stripped ref, or raise RefNameError.

    Rejects whitespace, ``..``, leading ``-``, control characters, ``@{``,
    backslash, and anything ``git check-ref-format --allow-onelevel`` rejects.
    """
    if not isinstance(name, str):
        raise RefNameError("ref name must be a string")
    value = name.strip()
    if not value:
        raise RefNameError("ref name is empty")
    if len(value) > _MAX_REF:
        raise RefNameError(f"ref name exceeds {_MAX_REF} characters")
    if name != value:
        raise RefNameError("ref name must not have leading or trailing whitespace")
    if any(ch.isspace() for ch in value):
        raise RefNameError("ref name must not contain whitespace")
    if _CTRL.search(value):
        raise RefNameError("ref name must not contain control characters")
    if ".." in value:
        raise RefNameError("ref name must not contain '..'")
    if value.startswith("-"):
        raise RefNameError("ref name must not start with '-'")
    if "@{" in value:
        raise RefNameError("ref name must not contain '@{'")
    if "\\" in value:
        raise RefNameError("ref name must not contain a backslash")
    proc = subprocess.run(
        ["git", "check-ref-format", "--allow-onelevel", value],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env=git_environ(),
    )
    if proc.returncode != 0:
        raise RefNameError("ref name failed git check-ref-format")
    return value


def resolve_commit(repo: Path, ref: str) -> str | None:
    """Return the commit SHA for ref, or None if it does not resolve."""
    proc = run_git(["rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=repo, timeout=30)
    if proc.returncode != 0:
        return None
    sha = proc.stdout.strip()
    return sha or None


def prepare_base_ref(repo: Path, base_ref: str, *, fetch_timeout: float = 120) -> tuple[str, str]:
    """Validate, optionally fetch, and resolve ``base_ref``.

    Returns ``(effective_ref, sha)``. Raises ``RefNameError`` for bad names,
    ``RuntimeError`` when fetch or resolve fails (caller maps to HTTP status).
    """
    ref = validate_ref_name(base_ref)
    if ref == "HEAD":
        sha = resolve_commit(repo, "HEAD")
        if not sha:
            raise RuntimeError("HEAD could not be resolved in the repository")
        return "HEAD", sha

    fetch = run_git(["fetch", "origin", ref], cwd=repo, timeout=fetch_timeout)
    if fetch.returncode != 0:
        detail = (fetch.stderr or fetch.stdout or "").strip().splitlines()
        hint = detail[-1] if detail else "git fetch failed"
        # Never echo a long remote blob; keep one short line.
        raise RuntimeError(f"could not fetch '{ref}' from origin: {hint[:200]}")

    for candidate in (ref, f"origin/{ref}", f"refs/remotes/origin/{ref}"):
        sha = resolve_commit(repo, candidate)
        if sha:
            return candidate, sha
    raise RuntimeError(f"base_ref '{ref}' could not be resolved after fetch")
