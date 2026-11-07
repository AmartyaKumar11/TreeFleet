"""Capture UI v2 Part 3 live/replay evidence under evidence/phase-8/ui-v2/live/."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]
DEMO_REPO = Path(r"D:\pr-sentinel-demo")
OUT = ROOT / "evidence" / "phase-8" / "ui-v2" / "live"
EVENTS = ROOT / "evidence" / "phase-8" / "events.jsonl"
GOAL = "Add unit tests for src/notifications.py and, as a second task, add unit tests for src/admin.py."
PORT = 8767


def _load_env() -> None:
    from treefleet.evals.harness import load_env

    load_env(ROOT)


def _serve(replay: Path | None) -> tuple[uvicorn.Server, threading.Thread]:
    from treefleet.ui.server import create_app

    app = create_app(DEMO_REPO, root=ROOT, replay=replay)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("UI server failed to start")
    return server, thread


def _shots(page, stem: str) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.wait_for_timeout(200)
    page.screenshot(path=str(OUT / f"{stem}-1440.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(200)
    page.screenshot(path=str(OUT / f"{stem}-390.png"), full_page=True)
    page.set_viewport_size({"width": 1440, "height": 900})


def capture_replay() -> int:
    _load_env()
    if not EVENTS.is_file():
        print("events.jsonl missing", file=sys.stderr)
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    server, thread = _serve(EVENTS)
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                record_video_dir=str(OUT),
                record_video_size={"width": 1440, "height": 900},
            )
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            mid = False
            deadline = time.time() + 240
            while time.time() < deadline:
                status = page.locator("#run-status").inner_text()
                if not mid and ("Running" in status or "Reviewing" in status or "Integrating" in status):
                    _shots(page, "mid-fleet")
                    mid = True
                if "Done" in status:
                    break
                time.sleep(1)
            if not mid:
                _shots(page, "mid-fleet")
            _shots(page, "final")
            page.close()
            context.close()
            browser.close()
            videos = sorted(OUT.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)
            if videos:
                target = OUT / "demo-run.webm"
                if target.exists():
                    target.unlink()
                videos[0].rename(target)
                for extra in videos[1:]:
                    if extra.name.startswith("page@"):
                        extra.unlink(missing_ok=True)
            print(f"replay evidence under {OUT}")
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    return 0


def capture_live() -> int:
    _load_env()
    if not DEMO_REPO.is_dir():
        print("demo repo missing", file=sys.stderr)
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    server, thread = _serve(None)
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                record_video_dir=str(OUT),
                record_video_size={"width": 1440, "height": 900},
            )
            page = context.new_page()
            page.goto(f"http://127.0.0.1:{PORT}/", wait_until="networkidle")
            page.locator("#goal").fill(GOAL)
            page.get_by_role("button", name="Run", exact=True).click()
            approved = False
            mid = False
            deadline = time.time() + 720
            while time.time() < deadline:
                status = page.locator("#run-status").inner_text()
                if (not approved) and "Awaiting approval" in status:
                    btn = page.get_by_role("button", name="Approve plan")
                    if btn.count():
                        btn.first.click()
                        approved = True
                if not mid and ("Running" in status or "Reviewing" in status):
                    _shots(page, "mid-fleet")
                    mid = True
                if "Done" in status or "Blocked" in status:
                    break
                time.sleep(2)
            if not mid:
                _shots(page, "mid-fleet")
            _shots(page, "final")
            page.close()
            context.close()
            browser.close()
            videos = sorted(OUT.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)
            if videos:
                target = OUT / "demo-run.webm"
                if target.exists():
                    target.unlink()
                videos[0].rename(target)
            print(f"live evidence under {OUT}")
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "replay"
    if mode == "live":
        return capture_live()
    return capture_replay()


if __name__ == "__main__":
    raise SystemExit(main())
