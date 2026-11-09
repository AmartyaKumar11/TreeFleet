"""Demo preflight and reset. Prints check names and fix commands, never a secret value."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

import httpx

from treefleet.config import MissingSecretError, load_settings
from treefleet.cursor_models import UnknownModelError, discover
from treefleet.cursor_run import launch_command
from treefleet.evals.harness import dotenv_values
from treefleet.git.worktrees import list_worktrees, remove_worktree

_KEYS = ("DEEPSEEK_API_KEY", "TYPESAFE_API_KEY")
_DEEPSEEK_MODELS = "https://api.deepseek.com/models"
_JEV_URL = "https://api.typesafe.ai/v1/systemone"
# A one-token classification. 401/403 means the key was rejected; 2xx means it was accepted.
_JEV_BODY = json.dumps(
    {"state": "ok", "model": "jev-latest", "questions": {"a": {"type": "noul", "instructions": "ok"}}}
)
_PROBES = (
    ("DeepSeek", "GET", _DEEPSEEK_MODELS, "DEEPSEEK_API_KEY", b""),
    ("Jev", "POST", _JEV_URL, "TYPESAFE_API_KEY", _JEV_BODY.encode()),
)


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


def _request_id(response: httpx.Response, key: str) -> str:
    """A provider request id. Drop it when it overlaps the key, including a prefix or suffix."""
    found: list[str] = []
    for name in ("x-request-id", "request-id", "x-requestid"):
        value = response.headers.get(name, "").strip()
        if value:
            found.append(value)
    try:
        body = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        body = None
    if isinstance(body, dict):
        for name in ("request_id", "requestId"):
            raw = body.get(name)
            if isinstance(raw, str) and raw.strip():
                found.append(raw.strip())
    for item in found:
        if len(item) < 8 or len(item) > 80 or not item.replace("-", "").isalnum():
            continue
        if key and (key in item or item in key):
            continue
        return item
    return ""


def _probe(client: httpx.Client, provider: str, method: str, url: str, key: str, body: bytes) -> tuple[bool, str]:
    headers = {"Authorization": f"Bearer {key}"}
    try:
        if method == "GET":
            response = client.get(url, headers=headers)
        else:
            headers["Content-Type"] = "application/json"
            response = client.post(url, content=body, headers=headers)
    except httpx.TimeoutException:
        return False, f"red    {provider} authentication check timed out"
    except httpx.HTTPError:
        return False, f"red    {provider} authentication check failed"
    if response.status_code in {401, 403}:
        ident = _request_id(response, key)
        suffix = f" request {ident}" if ident else ""
        return False, f"red    {provider} authentication failed{suffix}"
    if 200 <= response.status_code < 300:
        return True, f"green  {provider} authentication accepted"
    return False, f"red    {provider} authentication check failed"


def live_auth(env: Mapping[str, str], *, client: httpx.Client | None = None) -> list[tuple[bool, str]]:
    """Cheapest authenticated call for each provider. Timeout is 5 seconds. The key is never printed."""
    own = client is None
    if client is None:
        client = httpx.Client(timeout=5.0)
    rows: list[tuple[bool, str]] = []
    try:
        for provider, method, url, name, body in _PROBES:
            key = env.get(name) or ""
            if not key:
                continue
            rows.append(_probe(client, provider, method, url, key, body))
    finally:
        if own:
            client.close()
    return rows


def env_differs(env: Mapping[str, str], file_values: Mapping[str, str]) -> list[tuple[bool, str]]:
    """Report keys whose process value and .env value both exist and are not the same. Values stay unprinted."""
    rows: list[tuple[bool, str]] = []
    for key in _KEYS:
        process = env.get(key) or ""
        filed = file_values.get(key) or ""
        if process and filed and process != filed:
            rows.append((False, f"red    {key}: process env differs from .env"))
    return rows


def preflight(
    repo: Path,
    *,
    env: Mapping[str, str] | None = None,
    login: Callable[[Path], tuple[bool, str]] | None = None,
    models: Callable[[Path], tuple[bool, str]] | None = None,
    auth: Callable[[Mapping[str, str]], list[tuple[bool, str]]] | None = None,
    gh: Callable[[list[str]], tuple[int, str, str]] | None = None,
) -> list[tuple[bool, str]]:
    """One line per check: ok, and the text to print. The text has no secret value."""
    found = env if env is not None else os.environ
    lines: list[tuple[bool, str]] = []
    for key in _KEYS:
        if found.get(key):
            lines.append((True, f"green  {key} is set"))
        else:
            lines.append((False, f"red    {key} is unset\n       fix: set {key}"))
    lines.extend(env_differs(found, dotenv_values(Path.cwd())))
    if auth is not None:
        lines.extend(auth(found))
    elif env is None:
        lines.extend(live_auth(found))
    cli = Path("cursor-agent")
    settings = None
    try:
        settings = load_settings(Path.cwd() / "treefleet.toml")
        cli = Path(settings.cursor.cli)
    except (MissingSecretError, OSError):
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
    if gh is not None and settings is not None and settings.pr.enabled:
        from treefleet.pr.preflight import PreflightError, gh_smoke

        try:
            gh_smoke(settings.pr, gh)
        except PreflightError as exc:
            lines.append((False, f"red    {exc}"))
        else:
            lines.append((True, "green  gh can read the pull request repo"))
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


def _gh(args: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        shell=False,
        check=False,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


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
        rows = preflight(repo, gh=_gh)
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
