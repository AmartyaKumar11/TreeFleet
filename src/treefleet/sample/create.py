"""Create and reuse the local TreeFleet sample repo. No network."""

from __future__ import annotations

import subprocess
from importlib.resources import files
from pathlib import Path

MARKER = ".treefleet-sample"
BASE_BRANCH = "main"

# Disjoint write targets for the three sample tasks (tests already committed).
SAMPLE_TARGETS: dict[str, tuple[str, ...]] = {
    "add_one": ("tfsample/mathops.py",),
    "fix_greeter": ("tfsample/greeter.py",),
    "docs_greeter": ("docs/greeter.md",),
}

DEFAULT_GOAL = (
    "Make three independent changes that do not touch the same files: "
    "(1) add a working add_one(n) function in tfsample/mathops.py so tests/test_mathops.py passes; "
    "(2) fix the greeting bug in tfsample/greeter.py so tests/test_greeter.py passes; "
    "(3) add docs/greeter.md that documents the greeter module. "
    "Do not edit unrelated files."
)


def default_sample_dir() -> Path:
    return Path.home() / "treefleet-sample"


def _git(repo: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        shell=False,
        check=check,
    )


def _is_git(repo: Path) -> bool:
    return (repo / ".git").exists()


def _clean(repo: Path) -> bool:
    return not _git(repo, ["status", "--porcelain"], check=False).stdout.strip()


def _branch(repo: Path) -> str:
    return _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"], check=False).stdout.strip()


def _nonempty(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        next(path.iterdir())
    except StopIteration:
        return False
    return True


def _walk_templates(node: object, prefix: Path = Path()) -> list[tuple[Path, bytes]]:
    found: list[tuple[Path, bytes]] = []
    for child in node.iterdir():  # type: ignore[attr-defined]
        name = prefix / child.name
        if child.is_dir():
            found.extend(_walk_templates(child, name))
        else:
            found.append((name, child.read_bytes()))
    return found


def _copy_templates(dest: Path) -> list[str]:
    root = files("treefleet.sample").joinpath("templates")
    written: list[str] = []
    for rel, body in _walk_templates(root):
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        written.append(rel.as_posix())
    # Packaging may skip dotfiles; always ensure the marker exists.
    marker = dest / MARKER
    if not marker.is_file():
        marker.write_text("treefleet-sample 1\n", encoding="utf-8")
        written.append(MARKER)
    return written


def create_sample(dest: Path) -> list[str]:
    """Write templates, init git with local identity only, commit on main."""
    notes: list[str] = []
    dest.mkdir(parents=True, exist_ok=True)
    if any(dest.iterdir()):
        raise FileExistsError(f"{dest} is not empty")
    written = _copy_templates(dest)
    notes.append(f"wrote {len(written)} sample files")
    _git(dest, ["init", "-b", BASE_BRANCH])
    _git(dest, ["config", "user.email", "treefleet-sample@local"])
    _git(dest, ["config", "user.name", "TreeFleet Sample"])
    _git(dest, ["config", "core.autocrlf", "false"])
    _git(dest, ["add", "-A"])
    _git(dest, ["commit", "-m", "initial treefleet sample"])
    notes.append(f"sample ready at {dest} on {BASE_BRANCH}")
    return notes


def ensure_sample(dest: Path) -> tuple[int, list[str]]:
    """Create or reuse. Exit code 0 ok, 1 refuse. Never overwrites foreign trees."""
    notes: list[str] = []
    if not dest.exists() or not _nonempty(dest):
        try:
            dest.mkdir(parents=True, exist_ok=True)
            notes.extend(create_sample(dest))
        except FileExistsError:
            return 1, [f"{dest} is not empty; refusing to overwrite"]
        except OSError as exc:
            return 1, [f"could not create sample at {dest}: {exc}"]
        return 0, notes

    marker = dest / MARKER
    if not marker.is_file():
        return 1, [
            f"{dest} exists and is not empty, and is not a TreeFleet sample "
            f"(missing {MARKER}). Choose another --dir; refusing to overwrite."
        ]
    if not _is_git(dest):
        return 1, [f"{dest} has {MARKER} but is not a git repo; refusing"]
    if not _clean(dest):
        return 1, [f"{dest} is a TreeFleet sample but the working tree is dirty. Inspect with: git -C {dest} status"]
    branch = _branch(dest)
    if branch != BASE_BRANCH:
        return 1, [
            f"{dest} is a TreeFleet sample but on branch {branch!r}; "
            f"checkout {BASE_BRANCH} to reuse, or pick another --dir"
        ]
    notes.append(f"reusing clean sample at {dest}")
    return 0, notes


def targets_disjoint() -> bool:
    """True when no two sample tasks share a write path."""
    seen: set[str] = set()
    for paths in SAMPLE_TARGETS.values():
        for path in paths:
            if path in seen:
                return False
            seen.add(path)
    return True
