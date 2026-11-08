"""Capture the board from /api/board lane counts. Stub first, then one live run."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEMO = Path(r"D:\pr-sentinel-demo")
GOAL = "Add unit tests for src/notifications.py and, as a second task, add unit tests for src/admin.py."
TOKEN = "capture-token"
PORT = 8783
ORDER = ("working", "review", "ready")
LABELS = {"working": "Working", "review": "In review", "ready": "Ready to merge", "needs": "Needs you"}


def saw_lane_order(history: dict[str, list[str]]) -> bool:
    """True when one task visited working, then review, then ready, in that order."""
    for lanes in history.values():
        index = 0
        for lane in lanes:
            if index < len(ORDER) and lane == ORDER[index]:
                index += 1
        if index == len(ORDER):
            return True
    return False


def note_lanes(history: dict[str, list[str]], tasks: list[dict[str, Any]]) -> None:
    for task in tasks:
        task_id = str(task.get("id") or "")
        lane = str(task.get("lane") or "")
        if not task_id or not lane:
            continue
        seen = history.setdefault(task_id, [])
        if not seen or seen[-1] != lane:
            seen.append(lane)


def _prefer_dotenv() -> None:
    """Drop a process key that disagrees with .env. Values are never printed."""
    from treefleet.evals.harness import dotenv_values, load_env

    filed = dotenv_values(ROOT)
    for key in ("DEEPSEEK_API_KEY", "TYPESAFE_API_KEY", "CURSOR_API_KEY"):
        current = os.environ.get(key)
        on_disk = filed.get(key)
        if current and on_disk and current != on_disk:
            os.environ.pop(key, None)
            print(f"{key} from .env", flush=True)
    load_env(ROOT)


def _git(args: list[str]) -> str:
    proc = subprocess.run(
        ["git", "-C", str(DEMO), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        shell=False,
        check=False,
    )
    return (proc.stdout or proc.stderr).strip()


def _board(port: int) -> dict[str, Any]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/board",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def _approve(port: int, run_id: str) -> int:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/runs/{run_id}/approve",
        data=b"{}",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return int(response.status)


def _shot(page: Any, out: Path, stem: str, lane: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_timeout(150)
    page.screenshot(path=str(out / f"{stem}-1440.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(150)
    tab = page.locator(f'.mobile-lane-tabs button[data-lane="{lane}"]')
    if tab.count():
        tab.click()
        page.wait_for_timeout(100)
    page.screenshot(path=str(out / f"{stem}-390.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})


def capture(out: Path, *, live: bool) -> int:
    import uvicorn
    from playwright.sync_api import sync_playwright

    from treefleet.ui.server import create_app

    if live:
        _prefer_dotenv()
        os.environ.pop("TREEFLEET_STUB_FLEET", None)
        os.environ.pop("TREEFLEET_STUB_PAUSE", None)
    else:
        os.environ["TREEFLEET_STUB_FLEET"] = "1"
        os.environ["TREEFLEET_STUB_PAUSE"] = "2"
        os.environ["TREEFLEET_FAKE_SLEEP"] = "3"
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    head = _git(["rev-parse", "HEAD"])
    dirty = _git(["status", "--porcelain"])
    if branch != "demo-base" or dirty:
        print(f"demo branch {branch} dirty {bool(dirty)}", flush=True)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    (out / "head-before.txt").write_text(f"{branch} {head}\n", encoding="utf-8")
    app = create_app(DEMO, root=ROOT, token=TOKEN)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        print("UI server failed to start", flush=True)
        return 1
    history: dict[str, list[str]] = {}
    code = 1
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                record_video_dir=str(out),
                record_video_size={"width": 1440, "height": 900},
            )
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="domcontentloaded")
            page.locator("#goal").fill(GOAL)
            page.locator("#run").click()
            taken: set[str] = set()
            approved = False
            approve_at = 0.0
            limit = time.time() + (720 if live else 180)
            last = ""
            while time.time() < limit:
                try:
                    board = _board(PORT)
                except Exception as exc:
                    last = type(exc).__name__
                    time.sleep(0.3)
                    continue
                note_lanes(history, list(board.get("tasks") or []))
                stage = str(board.get("stage") or "")
                lanes = {str(task.get("lane") or "") for task in board.get("tasks") or []}
                last = f"{stage} {sorted(lanes)}"
                if "guard" not in taken and stage == "guard" and board.get("run_id"):
                    _shot(page, out, "guard", "needs")
                    taken.add("guard")
                if "plan-ready" not in taken and stage == "approve":
                    _shot(page, out, "plan-ready", "needs")
                    taken.add("plan-ready")
                    page.set_viewport_size({"width": 1440, "height": 900})
                    page.get_by_role("button", name="Approve plan").click()
                    approved = True
                    approve_at = time.time()
                if approved and stage == "approve" and time.time() - approve_at > 8:
                    run_id = str(board.get("run_id") or "")
                    print(f"approve click still on {stage}; HTTP approve", flush=True)
                    _approve(PORT, run_id)
                    approved = False
                if "fleet" not in taken and "working" in lanes:
                    _shot(page, out, "fleet", "working")
                    taken.add("fleet")
                if "review" not in taken and "review" in lanes:
                    _shot(page, out, "review", "review")
                    taken.add("review")
                if "ready" not in taken and "ready" in lanes:
                    _shot(page, out, "ready", "ready")
                    taken.add("ready")
                if stage == "done":
                    _shot(page, out, "done", "ready" if "ready" in lanes else "working")
                    taken.add("done")
                    break
                time.sleep(0.3)
            context.close()
            browser.close()
        recordings = sorted(out.glob("page@*.webm"), key=lambda path: path.stat().st_mtime)
        if recordings:
            shutil.copyfile(recordings[-1], out / "demo-run.webm")
        run_id = str(_board(PORT).get("run_id") or "")
        events = ROOT / "runs" / run_id / "events.jsonl"
        if events.is_file():
            shutil.copyfile(events, out / "events.jsonl")
        named = {task: [LABELS.get(lane, lane) for lane in lanes] for task, lanes in history.items()}
        (out / "lanes.json").write_text(json.dumps(named, indent=2) + "\n", encoding="utf-8")
        after = f"{_git(['rev-parse', '--abbrev-ref', 'HEAD'])} {_git(['rev-parse', 'HEAD'])}\n"
        (out / "head-after.txt").write_text(after, encoding="utf-8")
        print(json.dumps(named), flush=True)
        print(f"last {last}", flush=True)
        if "done" in taken and saw_lane_order(history):
            code = 0
        else:
            print("lane order was not Working, In review, Ready to merge", flush=True)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    return code


def main() -> int:
    live = "--live" in sys.argv
    out = ROOT / "evidence" / "phase-8" / "ui-v2" / "live-real-2"
    if not live:
        out = out / "stub"
    return capture(out, live=live)


if __name__ == "__main__":
    raise SystemExit(main())
