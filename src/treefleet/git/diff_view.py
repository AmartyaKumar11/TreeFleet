"""Board /api/diff payload. Repo content is untrusted; never run external/textconv drivers."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Literal

DEFAULT_CAP = 256 * 1024
FULL_CAP = 2 * 1024 * 1024
UNTRACKED_MAX = 512 * 1024
UNTRACKED_FILE_CAP = 200
# Hard cap plus slack when reading stdout from a git child.
_READ_SLACK = 64 * 1024
GIT_TIMEOUT_S = 10.0

Status = Literal["added", "modified", "deleted", "renamed", "copied", "mode_changed"]

# Exact prefixes / flags every invocation in this module uses.
_GIT_PREFIX = (
    "git",
    "--no-optional-locks",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "core.pager=cat",
    "-c",
    "core.quotepath=false",
    "-c",
    "diff.external=",
)

_SAFE_DIFF = ("--no-color", "--no-ext-diff", "--no-textconv")
_FIND_RENAMES = ("--find-renames",)
_U3 = ("-U3",)


def _scrub_env() -> dict[str, str]:
    env = {str(k): str(v) for k, v in os.environ.items()}
    for key in ("GIT_EXTERNAL_DIFF", "GIT_PAGER", "GIT_DIFF_OPTS"):
        env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    return env


def _null_device() -> str:
    return "NUL" if os.name == "nt" else "/dev/null"


def git_argv(*args: str) -> list[str]:
    """Build a full argv as this module would execute it (for tests/docs)."""
    return [*_GIT_PREFIX, *args]


def git_argv_literal(*args: str) -> list[str]:
    """Like git_argv but with global --literal-pathspecs (file= pathspecs stay literal)."""
    # git --literal-pathspecs --no-optional-locks ... <subcommand> ...
    return ["git", "--literal-pathspecs", *_GIT_PREFIX[1:], *args]


def _safe_git(
    args: list[str],
    cwd: Path,
    *,
    timeout: float = GIT_TIMEOUT_S,
    max_bytes: int | None = None,
    literal_pathspecs: bool = False,
) -> subprocess.CompletedProcess[str]:
    argv = git_argv_literal(*args) if literal_pathspecs else git_argv(*args)
    limit = (max_bytes if max_bytes is not None else FULL_CAP) + _READ_SLACK
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=_scrub_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    try:
        out_b, err_b = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out_b, err_b = proc.communicate()
    out_b = out_b or b""
    err_b = err_b or b""
    if len(out_b) > limit:
        # Bound was exceeded; drop the surplus (process already exited or was killed).
        out_b = out_b[:limit]
    # Keep bytes exactly; decode with replace. Do not use text=True (would normalize newlines).
    stdout = out_b.decode("utf-8", errors="replace")
    stderr = err_b.decode("utf-8", errors="replace")
    code = proc.returncode if proc.returncode is not None else -1
    return subprocess.CompletedProcess(argv, code, stdout, stderr)


def _is_binary_bytes(data: bytes) -> bool:
    return b"\0" in data[:8000]


def resolve_inside(worktree: Path, rel: str) -> Path | None:
    """Return resolved path if it stays inside worktree; else None. Public for unit tests."""
    try:
        root = worktree.resolve()
        # Reject escapes before resolve where possible.
        raw = rel.replace("\\", "/")
        if raw.startswith("/") or raw.startswith("//") or (len(raw) >= 2 and raw[1] == ":"):
            return None
        parts = Path(raw).parts
        if ".." in parts:
            return None
        target = (worktree / rel).resolve()
    except OSError:
        return None
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def _resolve_inside(worktree: Path, rel: str) -> Path | None:
    return resolve_inside(worktree, rel)


def _parse_numstat_line(line: str) -> tuple[str, str, int, int, bool] | None:
    # Keep \r if present on the line; strip only for field splits.
    line = line.rstrip("\n").rstrip("\r")
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
        left, right = path.split(" => ", 1)
        old_path = left.strip().lstrip("{").replace("\\", "/")
        path = right.strip().rstrip("}").replace("\\", "/")
        if "{" in left:
            prefix = left.split("{", 1)[0]
            old_path = f"{prefix}{old_path}"
            if not path.startswith(prefix):
                path = f"{prefix}{path}"
    return path, old_path, additions, deletions, binary


def _name_status(worktree: Path, base: str) -> dict[str, tuple[Status, str, int | None]]:
    # -M enables rename detection with similarity in the status code (R092 → 92).
    # `base` must already be a resolved SHA; pass after --end-of-options.
    proc = _safe_git(
        ["diff", *_SAFE_DIFF, "--name-status", "-M", *_FIND_RENAMES, "--end-of-options", base],
        cwd=worktree,
    )
    out: dict[str, tuple[Status, str, int | None]] = {}
    for line in (proc.stdout or "").split("\n"):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        parts = line.split("\t")
        if not parts:
            continue
        code = parts[0]
        letter = code[:1]
        sim: int | None = None
        if len(code) > 1 and code[1:].isdigit():
            sim = int(code[1:])
        if letter == "R" and len(parts) >= 3:
            out[parts[2].replace("\\", "/")] = ("renamed", parts[1].replace("\\", "/"), sim)
        elif letter == "C" and len(parts) >= 3:
            out[parts[2].replace("\\", "/")] = ("copied", parts[1].replace("\\", "/"), sim)
        elif letter == "A" and len(parts) >= 2:
            out[parts[1].replace("\\", "/")] = ("added", "", None)
        elif letter == "D" and len(parts) >= 2:
            out[parts[1].replace("\\", "/")] = ("deleted", "", None)
        elif letter == "T" and len(parts) >= 2:
            out[parts[1].replace("\\", "/")] = ("mode_changed", "", None)
        elif len(parts) >= 2:
            out[parts[1].replace("\\", "/")] = ("modified", "", None)
    return out


def _split_patches(text: str) -> list[tuple[str, str]]:
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
        for line in block.split("\n"):
            line_r = line.rstrip("\r")
            if line_r.startswith("+++ b/"):
                path = line_r[6:].strip()
                break
            if line_r.startswith("+++ /dev/null") or line_r.startswith("+++ NUL"):
                for prev in block.split("\n"):
                    prev_r = prev.rstrip("\r")
                    if prev_r.startswith("--- a/"):
                        path = prev_r[6:].strip()
                        break
                break
        if not path:
            head = block.split("\n")[0].rstrip("\r") if block else ""
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
    marker = b"\ndiff --git "
    cut = kept.rfind(marker)
    if cut > 0:
        kept = kept[:cut]
    nl = kept.rfind(b"\n")
    if nl >= 0:
        kept = kept[: nl + 1]
    return kept.decode("utf-8", errors="replace"), True, total


def _untracked_files(worktree: Path) -> tuple[list[str], bool]:
    """Return (paths, truncated). Uses -z so filenames with newlines stay intact."""
    proc = _safe_git(["ls-files", "-z", "--others", "--exclude-standard"], cwd=worktree)
    if proc.returncode != 0:
        return [], False
    raw = proc.stdout or ""
    paths = [p.replace("\\", "/") for p in raw.split("\0") if p.strip()]
    if len(paths) > UNTRACKED_FILE_CAP:
        return paths[:UNTRACKED_FILE_CAP], True
    return paths, False


def resolve_base_sha(worktree: Path, base: str) -> str:
    """Resolve base to a full 40-hex SHA. Never pass raw user/repo text as a git option."""
    candidate = (base or "HEAD").strip() or "HEAD"
    # Reject anything that could look like an option to git.
    if candidate.startswith("-") or "\0" in candidate or "\n" in candidate:
        candidate = "HEAD"
    proc = _safe_git(["rev-parse", "--verify", "--end-of-options", candidate], cwd=worktree)
    sha = (proc.stdout or "").strip().split("\n")[0].strip()
    if proc.returncode != 0 or len(sha) < 40 or any(c not in "0123456789abcdef" for c in sha.lower()):
        raise ValueError(f"could not resolve base ref safely: {base!r}")
    return sha.lower()


def _untracked_patch(worktree: Path, rel: str) -> tuple[str, bool, str]:
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
        ["diff", *_SAFE_DIFF, *_FIND_RENAMES, *_U3, "--no-index", "--", null, rel],
        cwd=worktree,
        max_bytes=FULL_CAP,
    )
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
    # Resolve to a full SHA so a poisoned base like "--output=…" can never become a git option.
    sha = resolve_base_sha(worktree, base)
    status_map = _name_status(worktree, sha)
    num = _safe_git(
        ["diff", *_SAFE_DIFF, "--numstat", *_FIND_RENAMES, "--end-of-options", sha],
        cwd=worktree,
    )
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line in (num.stdout or "").split("\n"):
        parsed = _parse_numstat_line(line)
        if not parsed:
            continue
        path, old_from_num, additions, deletions, binary = parsed
        status, old_path, similarity = status_map.get(path, ("modified", old_from_num, None))
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
        if similarity is not None and status in {"renamed", "copied"}:
            entry["similarity"] = similarity
        files.append(entry)
        seen.add(path)

    # When file_filter is set, run a targeted diff for that one path — never subject to the
    # whole-diff 256 KB preview cap (still bounded by FULL_CAP). Pathspecs stay literal.
    if file_filter:
        wanted = file_filter.replace("\\", "/").lstrip("./")
        files = [item for item in files if item["path"] == wanted]
        single_cap = FULL_CAP
        patch_text = ""
        file_truncated = False
        if files:
            item = files[0]
            # Final argv shape:
            # git --literal-pathspecs ... diff --no-color --no-ext-diff --no-textconv
            #   --find-renames -U3 --end-of-options <BASE> -- <path> [<old_path>]
            diff_args = [
                "diff",
                *_SAFE_DIFF,
                *_FIND_RENAMES,
                *_U3,
                "--end-of-options",
                sha,
                "--",
            ]
            if item["status"] == "renamed" and item.get("old_path"):
                diff_args.extend([str(item["old_path"]), wanted])
            else:
                diff_args.append(wanted)
            proc = _safe_git(diff_args, cwd=worktree, max_bytes=single_cap, literal_pathspecs=True)
            patch_text = proc.stdout or ""
            if len(patch_text.encode("utf-8", errors="replace")) >= single_cap:
                file_truncated = True

        untracked, _utrunc = _untracked_files(worktree)
        if wanted in untracked and wanted not in seen:
            patch, binary, flag = _untracked_patch(worktree, wanted)
            if flag != "skipped":
                entry = {
                    "path": wanted,
                    "status": "added",
                    "old_path": None,
                    "binary": binary or flag == "too_large",
                    "additions": 0 if binary or flag == "too_large" else patch.count("\n+") - patch.count("\n+++"),
                    "deletions": 0,
                }
                if flag == "too_large":
                    entry["too_large"] = True
                files = [entry]
                seen.add(wanted)
                if patch and not binary:
                    patch_text = patch if patch.endswith("\n") else patch + "\n"

        total_bytes = len(patch_text.encode("utf-8", errors="replace"))
        return {
            "diff": patch_text,
            "files": files,
            "truncated": file_truncated,
            "total_bytes": total_bytes,
            "shown_bytes": total_bytes,
            "untracked_truncated": False,
            "base_sha": sha,
        }

    # Original path: no file_filter, fetch all diffs
    patch_proc = _safe_git(
        ["diff", *_SAFE_DIFF, *_FIND_RENAMES, *_U3, "--end-of-options", sha],
        cwd=worktree,
        max_bytes=cap,
    )
    patch_text = patch_proc.stdout or ""

    untracked, untracked_truncated = _untracked_files(worktree)
    for rel in untracked:
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
    shown, truncated, total_bytes = _truncate_at_file_boundary(patch_text, cap)
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
        "untracked_truncated": untracked_truncated,
        "base_sha": sha,
    }
