"""Board /api/diff payload. Repo content is untrusted; never run external/textconv drivers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from treefleet.git.worktrees import run_git

DEFAULT_CAP = 256 * 1024
FULL_CAP = 2 * 1024 * 1024
UNTRACKED_MAX = 512 * 1024

Status = Literal["added", "modified", "deleted", "renamed", "copied", "mode_changed"]

_SAFE_CONFIG = (
    "-c",
    "core.quotepath=false",
    "-c",
    "diff.external=",
    "-c",
    "diff.suppressBlankEmpty=false",
)

_SAFE_FLAGS = ("--no-color", "--no-ext-diff", "--no-textconv", "--find-renames", "-U3")


def _safe_git(args: list[str], cwd: Path, timeout: float = 60) -> Any:
    return run_git([*_SAFE_CONFIG, *args], cwd=cwd, timeout=timeout)


def _null_device() -> str:
    return "NUL" if os.name == "nt" else "/dev/null"


def _is_binary_bytes(data: bytes) -> bool:
    return b"\0" in data[:8000]


def _resolve_inside(worktree: Path, rel: str) -> Path | None:
    try:
        root = worktree.resolve()
        target = (worktree / rel).resolve()
    except OSError:
        return None
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def _parse_numstat_line(line: str) -> tuple[str, str, int, int, bool] | None:
    parts = line.split("\t")
    if len(parts) < 3:
        return None
    add_s, del_s, path_raw = parts[0], parts[1], parts[-1]
    binary = add_s == "-" and del_s == "-"
    additions = 0 if binary or not add_s.isdigit() else int(add_s)
    deletions = 0 if binary or not del_s.isdigit() else int(del_s)
    path = path_raw.replace("\\", "/")
    old_path = ""
    if " => " in path:
        # rename forms: {old => new} or old => new
        left, right = path.split(" => ", 1)
        old_path = left.strip().lstrip("{").replace("\\", "/")
        path = right.strip().rstrip("}").replace("\\", "/")
        if "{" in left:
            prefix = left.split("{", 1)[0]
            old_path = f"{prefix}{old_path}"
            if not path.startswith(prefix):
                path = f"{prefix}{path}"
    return path, old_path, additions, deletions, binary


def _name_status(worktree: Path, base: str) -> dict[str, tuple[Status, str]]:
    proc = _safe_git(["diff", "--name-status", "--find-renames", base], cwd=worktree)
    out: dict[str, tuple[Status, str]] = {}
    for line in (proc.stdout or "").splitlines():
        parts = line.split("\t")
        if not parts:
            continue
        code = parts[0][:1]
        if code == "R" and len(parts) >= 3:
            out[parts[2].replace("\\", "/")] = ("renamed", parts[1].replace("\\", "/"))
        elif code == "C" and len(parts) >= 3:
            out[parts[2].replace("\\", "/")] = ("copied", parts[1].replace("\\", "/"))
        elif code == "A" and len(parts) >= 2:
            out[parts[1].replace("\\", "/")] = ("added", "")
        elif code == "D" and len(parts) >= 2:
            out[parts[1].replace("\\", "/")] = ("deleted", "")
        elif code == "T" and len(parts) >= 2:
            out[parts[1].replace("\\", "/")] = ("mode_changed", "")
        elif len(parts) >= 2:
            out[parts[1].replace("\\", "/")] = ("modified", "")
    return out


def _split_patches(text: str) -> list[tuple[str, str]]:
    """Return [(path, patch)] in order. Path may be empty for preamble-only chunks."""
    if not text:
        return []
    chunks: list[tuple[str, str]] = []
    parts = text.split("\ndiff --git ")
    for i, part in enumerate(parts):
        block = part if i == 0 else "diff --git " + part
        if i == 0 and not block.startswith("diff --git "):
            if block.strip():
                chunks.append(("", block))
            continue
        path = ""
        for line in block.splitlines():
            if line.startswith("+++ b/"):
                path = line[6:].strip()
                break
            if line.startswith("+++ /dev/null") or line.startswith("+++ NUL"):
                for prev in block.splitlines():
                    if prev.startswith("--- a/"):
                        path = prev[6:].strip()
                        break
                break
        if not path:
            # rename header: diff --git a/old b/new
            head = block.splitlines()[0] if block else ""
            if " b/" in head:
                path = head.split(" b/", 1)[-1].strip()
        chunks.append((path.replace("\\", "/"), block if block.endswith("\n") else block + "\n"))
    return chunks


def _truncate_at_file_boundary(text: str, cap: int) -> tuple[str, bool, int]:
    total = len(text.encode("utf-8", errors="replace"))
    if total <= cap:
        return text, False, total
    encoded = text.encode("utf-8", errors="replace")
    kept = encoded[:cap]
    # back up to last complete patch header boundary
    marker = b"\ndiff --git "
    cut = kept.rfind(marker)
    if cut > 0:
        kept = kept[:cut]
    # never mid-line
    nl = kept.rfind(b"\n")
    if nl >= 0:
        kept = kept[: nl + 1]
    return kept.decode("utf-8", errors="replace"), True, total


def _untracked_files(worktree: Path) -> list[str]:
    proc = _safe_git(["ls-files", "--others", "--exclude-standard"], cwd=worktree)
    if proc.returncode != 0:
        return []
    return [line.strip().replace("\\", "/") for line in (proc.stdout or "").splitlines() if line.strip()]


def _untracked_patch(worktree: Path, rel: str) -> tuple[str, bool, str]:
    """Return (patch, binary, flag) where flag is '' | 'too_large' | 'skipped'."""
    target = _resolve_inside(worktree, rel)
    if target is None or target.is_symlink() or not target.is_file():
        return "", False, "skipped"
    try:
        size = target.stat().st_size
    except OSError:
        return "", False, "skipped"
    if size > UNTRACKED_MAX:
        return "", True, "too_large"
    try:
        data = target.read_bytes()
    except OSError:
        return "", False, "skipped"
    if _is_binary_bytes(data):
        return "", True, ""
    null = _null_device()
    proc = _safe_git(
        ["diff", *_SAFE_FLAGS, "--no-index", "--", null, rel],
        cwd=worktree,
    )
    # git diff --no-index returns 1 when files differ
    text = proc.stdout or ""
    if "diff --git" not in text and text.strip():
        text = f"diff --git a/{rel} b/{rel}\n--- /dev/null\n+++ b/{rel}\n" + text
    return text, False, ""


def build_diff_payload(
    worktree: Path,
    base: str,
    *,
    full: bool = False,
    file_filter: str | None = None,
) -> dict[str, Any]:
    cap = FULL_CAP if full else DEFAULT_CAP
    status_map = _name_status(worktree, base)
    num = _safe_git(["diff", "--numstat", "--find-renames", base], cwd=worktree)
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in (num.stdout or "").splitlines():
        parsed = _parse_numstat_line(line)
        if not parsed:
            continue
        path, old_from_num, additions, deletions, binary = parsed
        status, old_path = status_map.get(path, ("modified", old_from_num))
        if old_from_num and not old_path:
            old_path = old_from_num
        entry: dict[str, Any] = {
            "path": path,
            "status": status,
            "old_path": old_path or None,
            "binary": binary,
            "additions": additions,
            "deletions": deletions,
        }
        files.append(entry)
        seen.add(path)

    patch_proc = _safe_git(["diff", *_SAFE_FLAGS, base], cwd=worktree)
    patch_text = patch_proc.stdout or ""

    for rel in _untracked_files(worktree):
        if rel in seen:
            continue
        patch, binary, flag = _untracked_patch(worktree, rel)
        if flag == "skipped":
            continue
        entry = {
            "path": rel,
            "status": "added",
            "old_path": None,
            "binary": binary or flag == "too_large",
            "additions": 0 if binary or flag == "too_large" else patch.count("\n+") - patch.count("\n+++"),
            "deletions": 0,
        }
        if flag == "too_large":
            entry["too_large"] = True
        files.append(entry)
        seen.add(rel)
        if patch and not binary:
            if patch_text and not patch_text.endswith("\n"):
                patch_text += "\n"
            patch_text += patch if patch.endswith("\n") else patch + "\n"

    files.sort(key=lambda item: str(item["path"]))
    if file_filter:
        wanted = file_filter.replace("\\", "/").lstrip("./")
        files = [item for item in files if item["path"] == wanted]
        chunks = _split_patches(patch_text)
        patch_text = "".join(block for path, block in chunks if path == wanted or (not path and wanted in block))

    shown, truncated, total_bytes = _truncate_at_file_boundary(patch_text, cap)
    # When truncated, drop file rows whose patch was cut (keep metadata for files still present)
    if truncated:
        present = {path for path, _ in _split_patches(shown) if path}
        if present:
            files = [item for item in files if item["path"] in present or item.get("binary")]

    return {
        "diff": shown,
        "files": files,
        "truncated": truncated,
        "total_bytes": total_bytes,
        "shown_bytes": len(shown.encode("utf-8", errors="replace")),
    }
