"""Persist the UI bearer token in an owner-only file. Never log the value."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path


class TokenFileError(RuntimeError):
    """The token file is missing, empty, or has an unsafe mode."""


def load_or_create_token(path: Path) -> str:
    """Read an existing token file, or create one atomically at mode 0600.

    On POSIX, an existing file must be owner-read/write only (no group/other
    bits). On Windows the mode check is skipped. The token value is never
    written to logs by this helper.
    """
    target = Path(path)
    if target.is_file():
        _require_owner_only(target)
        token = target.read_text(encoding="utf-8").strip()
        if not token:
            raise TokenFileError("token file is empty")
        return token

    target.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(18)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(str(target), flags, 0o600)
    except FileExistsError:
        return load_or_create_token(target)
    try:
        os.write(fd, token.encode("utf-8"))
    finally:
        os.close(fd)
    if os.name != "nt":
        os.chmod(target, 0o600)
    return token


def _require_owner_only(path: Path) -> None:
    if os.name == "nt":
        return
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise TokenFileError("token file must be mode 0600 (owner only)")
