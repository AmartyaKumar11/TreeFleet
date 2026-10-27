"""S7: Windows git worktree add/remove, psutil cwd, process-tree kill, autocrlf."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid
from importlib.metadata import version
from pathlib import Path

import psutil

ROOT = Path(r"D:\tf") / uuid.uuid4().hex[:6]


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, shell=False,
    )


def main() -> None:
    print(f"psutil {version('psutil')}")
    if ROOT.exists():
        shutil.rmtree(ROOT, ignore_errors=True)
    repo, wt = ROOT / "repo", ROOT / "wt"
    repo.mkdir(parents=True)
    git("init", "-b", "main", str(repo))
    git("config", "--local", "core.autocrlf", "false", cwd=repo)
    autocrlf = git("config", "--local", "--get", "core.autocrlf", cwd=repo).stdout.strip()
    (repo / "README.md").write_text("base\n", encoding="utf-8", newline="\n")
    git("add", "README.md", cwd=repo)
    git("-c", "user.email=s7@example.com", "-c", "user.name=s7", "commit", "-m", "base", cwd=repo)
    git("worktree", "add", "-b", "spike/s7", str(wt), "HEAD", cwd=repo)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=wt, shell=False)
    time.sleep(0.5)
    proc = psutil.Process(child.pid)
    seen = [item.cwd() for item in [proc, *proc.children(recursive=True)]]
    cwd_ok = any(os.path.normcase(item) == os.path.normcase(str(wt)) for item in seen)
    grandchild = subprocess.Popen(
        [sys.executable, "-c", "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); time.sleep(60)"],
        cwd=wt, shell=False,
    )
    time.sleep(0.5)
    parent = psutil.Process(grandchild.pid)
    kids = parent.children(recursive=True)
    parent.kill()
    for proc in kids:
        proc.kill()
    parent.wait(timeout=5)
    gone = not parent.is_running() and all(not proc.is_running() for proc in kids)
    child.kill()
    child.wait(timeout=5)
    git("worktree", "remove", "--force", str(wt), cwd=repo)
    removed = not wt.exists()
    print(f"autocrlf={autocrlf} path_len={len(str(wt))} cwd={seen} cwd_ok={cwd_ok} tree_killed={gone} removed={removed} children={len(kids)}")
    shutil.rmtree(ROOT, ignore_errors=True)
    if autocrlf == "false" and len(str(wt)) < 120 and cwd_ok and gone and removed and kids:
        print("Result: PASS")
    else:
        print("Result: FALLBACK shorten the worktree root, kill by job object")


if __name__ == "__main__":
    main()
