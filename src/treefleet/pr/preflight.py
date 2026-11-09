"""Checks that run only when [pr] is enabled. A warning does not stop the run."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from treefleet.pr.config import PrSettings
from treefleet.pr.safety import urls_match

_GITHUB_HOSTS = {"github.com", "api.github.com", "www.github.com"}
Runner = Callable[[list[str]], tuple[int, str, str]]


class PreflightError(RuntimeError):
    """A required GitHub check failed. The message has no tokens."""


def hook_host(url: str) -> str:
    parsed = urlparse(url.strip())
    return (parsed.hostname or "").lower()


def external_hook_block(settings: PrSettings, hosts: list[str]) -> str | None:
    """Refuse to open PRs while an outside hook is listening, until the switch is true."""
    if settings.allow_external_hooks:
        return None
    for host in hosts:
        if host and host not in _GITHUB_HOSTS:
            return "an external pull request hook is configured; set pr.allow_external_hooks = true to open PRs"
    return None


def preflight(
    repo: Path,
    settings: PrSettings,
    *,
    runner: Runner,
    remote_url: str,
    hook_hosts: list[str] | None = None,
) -> list[str]:
    """Red checks raise. A missing workflow is a warning string."""
    if not settings.enabled:
        return []
    code, out, err = runner(["auth", "status"])
    if code != 0:
        raise PreflightError("gh auth status failed")
    code, login_out, _err = runner(["api", "user", "--jq", ".login"])
    login = login_out.strip()
    if code != 0 or not login or "/" in login:
        raise PreflightError("gh is not authenticated")
    code, perm_out, _err = runner(["api", f"repos/{settings.repo}", "--jq", ".permissions.push"])
    if code != 0 or perm_out.strip().lower() not in {"true", "1"}:
        raise PreflightError(f"no push permission on {settings.repo}")
    if not urls_match(remote_url, settings.repo):
        raise PreflightError("origin does not match pr.repo")
    code, _base_out, _err = runner(["api", f"repos/{settings.repo}/branches/{settings.base}"])
    if code != 0:
        raise PreflightError(f"base branch {settings.base} is not on {settings.repo}")
    warnings: list[str] = []
    code, flow_out, _err = runner(["api", f"repos/{settings.repo}/contents/.github/workflows?ref={settings.base}"])
    names = _workflow_names(flow_out) if code == 0 else []
    if not names:
        warnings.append(f"warning: no workflow on {settings.base}")
    hosts = list(hook_hosts or [])
    blocked = external_hook_block(settings, hosts)
    if blocked:
        raise PreflightError(blocked)
    gh_smoke(settings, runner)
    return warnings


def gh_smoke(settings: PrSettings, runner: Runner) -> None:
    """Read the configured repo and an empty sentinel head. Never another pull request."""
    code, _out, _err = runner(["api", f"repos/{settings.repo}"])
    if code != 0:
        raise PreflightError(f"gh cannot read {settings.repo}")
    code, listed, _err = runner(
        ["pr", "list", "--repo", settings.repo, "--head", "tf/__preflight__", "--json", "number"]
    )
    if code != 0:
        raise PreflightError("gh pr list preflight failed")
    try:
        payload = json.loads(listed or "[]")
    except json.JSONDecodeError as exc:
        raise PreflightError("gh pr list preflight returned non-json") from exc
    if payload != []:
        raise PreflightError("preflight head matched a pull request")


def _workflow_names(body: str) -> list[str]:
    try:
        payload = json.loads(body or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item.get("name")) for item in payload if isinstance(item, dict) and item.get("name")]
