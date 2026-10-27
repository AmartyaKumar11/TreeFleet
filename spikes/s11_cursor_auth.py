"""S11: cursor-agent API-key auth on this machine. Container check is skipped. Never prints secrets."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def launcher() -> list[str]:
    root = Path(os.environ["LOCALAPPDATA"]) / "cursor-agent"
    ps1 = root / "cursor-agent.ps1"
    pwsh = Path(os.environ["SYSTEMROOT"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return [str(pwsh), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)]


def run(args: list[str]) -> str:
    completed = subprocess.run(
        launcher() + args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, shell=False,
    )
    return (completed.stdout or "") + (completed.stderr or "")


def main() -> None:
    help_text = run(["--help"])
    status = run(["status"])
    print(f"api_key_flag={'--api-key' in help_text}")
    print(f"env_CURSOR_API_KEY={'set' if os.environ.get('CURSOR_API_KEY') else 'unset'}")
    print(f"logged_in={'Logged in' in status}")
    print("container=skipped")
    print("Result: FALLBACK run the demo locally")


if __name__ == "__main__":
    main()
