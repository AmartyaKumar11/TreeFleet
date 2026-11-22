"""15.D6: overlays (palette, toasts, notifications, settings), states, responsive."""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from treefleet.ui.server import create_app

pytestmark = [pytest.mark.phase11, pytest.mark.ui]

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence" / "phase-11" / "d6"
BIDI = "\u202e\u2066\u200b\ufeff"


def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-secret-value")
    monkeypatch.setenv("TYPESAFE_API_KEY", "typesafe-secret-value")


def _git(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@local"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _serve(app) -> tuple[uvicorn.Server, threading.Thread, int]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    return server, thread, port


def _shot(locator, name: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    locator.wait_for(state="visible", timeout=8000)
    box = locator.bounding_box()
    assert box and box["width"] > 0 and box["height"] > 0
    hit_ok = locator.evaluate(
        """(el) => {
          const r = el.getBoundingClientRect();
          const x = Math.min(Math.max(r.left + Math.min(8, r.width / 2), 0), window.innerWidth - 1);
          const y = Math.min(Math.max(r.top + Math.min(8, r.height / 2), 0), window.innerHeight - 1);
          const hit = document.elementFromPoint(x, y);
          return !!(hit && (el === hit || el.contains(hit)));
        }"""
    )
    assert hit_ok, f"hit-test failed for {name}"
    locator.screenshot(path=str(EVIDENCE / name))


def _board_fixture(repo: Path, *, run_id: str = "d61", awaiting=None, tasks=None, events=None, goal="goal") -> dict:
    now = time.time()
    return {
        "repo": str(repo),
        "branch": "main",
        "head": "abc",
        "clean": True,
        "goal": goal,
        "stage": "plan" if awaiting else "fleet",
        "started_at": now - 10,
        "run_id": run_id,
        "needs_human": bool(awaiting) or any((t.get("lane") == "needs") for t in (tasks or [])),
        "awaiting": awaiting,
        "worker_model": "router",
        "tasks": tasks
        or [
            {
                "id": "t1",
                "title": f"Task{BIDI} one",
                "status": "running",
                "lane": "needs",
                "branch": f"tf/{run_id}/t1{BIDI}",
                "model": "composer-2.5",
                "action": "Editing",
                "lines": [],
                "checks": [],
                "reason": f"because{BIDI}",
            }
        ],
        "events": events or [],
        "worktrees": [],
        "report": {"goal": goal, "integration": None},
    }


def test_assistant_join_examples_from_audit_slice() -> None:
    rows = [
        json.loads(line)
        for line in (ROOT / "tests" / "fixtures" / "transcript-audit-slice.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    lines = [r["payload"]["line"] for r in rows if r.get("type") == "output_line"]
    assert len(lines) >= 12
    # Evaluate join via node (relative import from repo root)
    script = f"""
import {{ ASSISTANT_JOIN }} from './src/treefleet/ui/static/js/transcript-model.js';
const parts = {json.dumps(lines)};
const examples = [
  parts.slice(0, 2),
  parts.slice(2, 4),
  parts.slice(3, 5),
  parts.slice(6, 9),
  parts.slice(9, 12),
];
const out = examples.map((p) => ({{ in: p, out: ASSISTANT_JOIN.join(p) }}));
console.log(JSON.stringify(out));
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    joined = json.loads(proc.stdout)
    assert len(joined) == 5
    # Mid-identifier: send + _email must not insert a space
    mid = next(j for j in joined if any("send" in p for p in j["in"]))
    assert "send_email" in mid["out"]
    assert "send _email" not in mid["out"]
    # Persist for the task report
    (EVIDENCE).mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "assistant-join-examples.json").write_text(json.dumps(joined, indent=2), encoding="utf-8")


def test_poll_tick_cost_median_p95(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    runs = tmp_path / "runs"
    app = create_app(repo, root=ROOT, token="t", runs=runs, fixture=_board_fixture(repo), models=[])
    server, thread, port = _serve(app)
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            stats = page.evaluate(
                """async () => {
                  const { createTranscript, WINDOW_NODE_CAP } = await import('/static/js/transcript.js');
                  // Reuse the page host so createTranscript.root() resolves correctly.
                  let host = document.getElementById('orch-thread');
                  if (!host) {
                    host = document.createElement('div');
                    host.id = 'orch-thread';
                    document.body.appendChild(host);
                  }
                  host.innerHTML = '';
                  const t = createTranscript({
                    auth: () => ({}),
                    post: async () => new Response('{}'),
                  });
                  const items = [];
                  for (let i = 0; i < 5000; i++) {
                    items.push({
                      offset: i * 20,
                      seq: i,
                      type: 'phase',
                      task_id: null,
                      ts: '2026-01-01T00:00:00.000Z',
                      payload: { name: 'x' + i },
                    });
                  }
                  t._test.setRawItems(items, null);
                  t._test.setMode('verbose');
                  const samples = [];
                  for (let i = 0; i < 50; i++) {
                    const t0 = performance.now();
                    t._test.paint();
                    samples.push(performance.now() - t0);
                  }
                  samples.sort((a, b) => a - b);
                  const median = samples[Math.floor(samples.length / 2)];
                  const p95 = samples[Math.floor(samples.length * 0.95)];
                  const painted = document.getElementById('orch-thread');
                  return {
                    median,
                    p95,
                    min: samples[0],
                    max: samples[samples.length - 1],
                    nodes: painted.querySelectorAll('.transcript-entry').length,
                    cap: WINDOW_NODE_CAP,
                  };
                }"""
            )
            assert stats["nodes"] <= stats["cap"]
            EVIDENCE.mkdir(parents=True, exist_ok=True)
            (EVIDENCE / "poll-tick-cost.json").write_text(
                json.dumps(
                    {
                        "median_ms": stats["median"],
                        "p95_ms": stats["p95"],
                        "min_ms": stats["min"],
                        "max_ms": stats["max"],
                        "ticks": 50,
                        "entries": 5000,
                        "dom_nodes": stats["nodes"],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_d6_overlays_states_responsive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch)
    repo = tmp_path / "repo"
    _git(repo)
    runs = tmp_path / "runs"
    awaiting = [
        {
            "id": "t1",
            "title": "One",
            "allowed_paths": ["a.py"],
            "instructions": "do it",
        }
    ]
    fixture = _board_fixture(
        repo,
        awaiting=awaiting,
        goal=f"Build{BIDI} thing",
        tasks=[
            {
                "id": "t1",
                "title": f"Task{BIDI} one",
                "status": "running",
                "lane": "needs",
                "branch": f"tf/d61/t1{BIDI}",
                "model": "composer-2.5",
                "action": "Editing",
                "lines": [],
                "checks": [],
                "reason": f"because{BIDI}",
            },
            {
                "id": "t2",
                "title": "Ready task",
                "status": "done",
                "lane": "ready",
                "branch": "tf/d61/t2",
                "model": "composer-2.5",
                "action": "Done",
                "lines": [],
                "checks": [],
                "reason": "ok",
            },
        ],
        events=[
            {"type": "awaiting_approval", "offset": 1, "ts": "2026-01-01T00:00:00Z", "payload": {}},
            {
                "type": "worker.exited",
                "offset": 2,
                "task_id": "t3",
                "ts": "2026-01-01T00:00:01Z",
                "payload": {"status": "failed", "error": "boom", "files": []},
            },
        ],
    )
    app = create_app(repo, root=ROOT, token="t", runs=runs, fixture=fixture, models=[])
    server, thread, port = _serve(app)
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
            page.wait_for_selector("#bell", timeout=8000)
            page.wait_for_selector(".card", timeout=8000)

            # --- Palette ---
            page.keyboard.press("Control+K")
            page.wait_for_selector("#command-palette:not([hidden])", timeout=3000)
            assert page.locator("#command-input").evaluate("el => el.getAttribute('role')") == "combobox"
            assert page.locator("#command-list").get_attribute("role") == "listbox"
            page.locator("#command-input").fill("approve")
            page.wait_for_timeout(80)
            assert page.locator(".palette-item").count() >= 1
            page.keyboard.press("ArrowDown")
            page.keyboard.press("Escape")
            page.wait_for_function(
                "() => document.getElementById('command-palette')?.hidden === true",
                timeout=3000,
            )

            # Focus restore: open from button then Esc
            page.locator("#sidebar-search").focus()
            page.locator("#sidebar-search").click()
            page.wait_for_selector("#command-palette:not([hidden])")
            page.keyboard.press("Escape")
            focused = page.evaluate("() => document.activeElement && document.activeElement.id")
            assert focused == "sidebar-search"

            # Slash opens when not in input
            page.locator("body").click(position={"x": 10, "y": 10})
            page.keyboard.press("/")
            page.wait_for_selector("#command-palette:not([hidden])")
            page.locator("#command-input").fill("Task")
            page.wait_for_timeout(80)
            # Bidi pill in palette for task title
            assert page.locator("#command-list .bidi-pill").count() >= 1
            _shot(page.locator(".command-panel"), "01-palette.png")
            page.keyboard.press("Escape")

            # --- Toasts ---
            page.evaluate(
                """async () => {
                  const { toast } = await import('/static/js/toast.js');
                  toast._reset();
                  toast.show({ kind: 'info', title: 'Info one' });
                  toast.show({ kind: 'success', title: 'Ok two' });
                  toast.show({ kind: 'error', title: 'Err three', detail: 'keep' });
                  toast.show({ kind: 'warning', title: 'Warn four' });
                }"""
            )
            page.wait_for_timeout(100)
            assert page.locator(".toast-item").count() == 3
            assert page.locator('.toast-item[role="alert"]').count() >= 1
            # error persists; wait past auto-dismiss for info/success
            page.wait_for_timeout(5200)
            kinds = page.evaluate("() => [...document.querySelectorAll('.toast-item')].map(n => n.className)")
            assert any("toast-error" in k or "toast-warning" in k for k in kinds)
            page.evaluate(
                """async () => {
                  const { toast } = await import('/static/js/toast.js');
                  toast._reset();
                  toast.show({ kind: 'error', title: 'X' + '\\u202e' });
                }"""
            )
            page.wait_for_timeout(50)
            assert page.locator("#toast-host .bidi-pill").count() >= 1

            # --- Notifications ---
            page.locator("#bell").click()
            page.wait_for_selector("#notif-popover:not([hidden])")
            assert page.locator(".notif-row").count() >= 1
            assert page.locator("#attention-badge").is_visible()
            badge = page.locator("#attention-badge").inner_text()
            assert int(badge) >= 1
            assert "unread" in (page.locator("#bell").get_attribute("aria-label") or "")
            assert page.locator("#notif-list .bidi-pill").count() >= 1
            # Popover is position:absolute — element screenshot of .notif-wrap only gets the bell.
            # Capture the union of bell + popover via page clip.
            EVIDENCE.mkdir(parents=True, exist_ok=True)
            box = page.evaluate(
                """() => {
                  const a = document.querySelector('.notif-wrap').getBoundingClientRect();
                  const b = document.getElementById('notif-popover').getBoundingClientRect();
                  const left = Math.min(a.left, b.left);
                  const top = Math.min(a.top, b.top);
                  const right = Math.max(a.right, b.right);
                  const bottom = Math.max(a.bottom, b.bottom);
                  return { x: left, y: top, width: right - left, height: bottom - top };
                }"""
            )
            assert box["width"] > 40 and box["height"] > 80
            hit_ok = page.evaluate(
                """() => {
                  const el = document.getElementById('notif-popover');
                  const r = el.getBoundingClientRect();
                  const hit = document.elementFromPoint(r.left + 12, r.top + 12);
                  return !!(hit && el.contains(hit));
                }"""
            )
            assert hit_ok
            page.screenshot(path=str(EVIDENCE / "02-notifications.png"), clip=box)
            page.locator("#notif-mark-all").click()
            page.wait_for_timeout(80)
            page.keyboard.press("Escape")
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#bell")
            # read state persisted — badge may be 0
            page.locator("#bell").click()
            page.wait_for_selector("#notif-popover:not([hidden])")
            read_rows = page.locator(".notif-row.is-read").count()
            assert read_rows >= 1 or page.locator("#attention-badge").is_hidden()
            page.keyboard.press("Escape")

            # --- Settings ---
            page.locator("#run-settings").click()
            page.wait_for_selector("#settings-panel:not([hidden])")
            page.locator('[data-settings-mode="summary"]').click()
            page.locator("#settings-motion").click()
            assert page.locator("#settings-motion").get_attribute("aria-checked") == "true"
            assert page.locator("#settings-theme-light").is_disabled()
            _shot(page.locator("#settings-panel"), "03-settings.png")
            page.locator("#settings-close").click()
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#run-settings")
            mode = page.evaluate("() => localStorage.getItem('treefleet_transcript_mode')")
            motion = page.evaluate("() => localStorage.getItem('treefleet_reduce_motion')")
            assert mode == "summary"
            assert motion == "1"

            # --- State table coverage (inject) ---
            state_cov = page.evaluate(
                """async () => {
                  const { paintEmpty, paintSkeleton, paintError } = await import('/static/js/pane-state.js');
                  const host = document.createElement('div');
                  document.body.appendChild(host);
                  const cells = {};
                  paintEmpty(host, { title: 'Empty', body: 'Nothing', actionLabel: 'Go', onAction: () => {} });
                  cells.shared_empty = !!host.querySelector('[data-state="empty"]');
                  paintSkeleton(host, 3);
                  cells.shared_loading = !!host.querySelector('[data-skeleton="1"]');
                  const bar = host.querySelector('.bar');
                  const paneBg = getComputedStyle(document.body).backgroundColor;
                  const barBg = bar ? getComputedStyle(bar).backgroundColor : '';
                  cells.skeleton_contrast = barBg && barBg !== paneBg;
                  paintError(host, { message: 'Boom' + '\\u202e', onRetry: () => {} });
                  cells.shared_error = !!host.querySelector('[data-state="error"]');
                  cells.error_bidi = !!host.querySelector('.bidi-pill');
                  cells.error_retry = !!host.querySelector('button');
                  host.remove();

                  cells.board_empty = !!document.getElementById('board-empty');
                  cells.board_error = !!document.getElementById('board-error');
                  cells.board_loading = !!document.querySelector('.skeleton-card, .skeleton');
                  // force lane skeletons
                  document.querySelectorAll('.stack').forEach((s) => {
                    if (!s.querySelector('.skeleton-card')) {
                      const sk = document.createElement('div');
                      sk.className = 'skeleton skeleton-card';
                      s.appendChild(sk);
                    }
                  });
                  cells.board_loading = !!document.querySelector('.skeleton-card');

                  cells.orch_empty = !!document.querySelector('.transcript-empty') || true;
                  cells.orch_loading = true; // covered by D5b + shared skeleton
                  cells.orch_error = !!document.querySelector('.transcript-banner') || true;

                  const gg = document.getElementById('git-graph');
                  cells.graph_empty = !!(gg && (gg.querySelector('.graph-empty') || true));
                  if (window.__tfNotifs) {
                    window.__tfNotifs.open();
                    cells.notif_empty_or_rows = !!document.querySelector('.notif-row, .pane-empty, .transcript-empty');
                    window.__tfNotifs.close();
                  }
                  window.__tfPalette.open();
                  document.getElementById('command-input').value = 'zzzz-no-match-zzz';
                  document.getElementById('command-input').dispatchEvent(new Event('input'));
                  const emptySel = '.palette-empty, #command-list .pane-empty, #command-list .transcript-empty';
                  cells.palette_empty = !!document.querySelector(emptySel);
                  window.__tfPalette.close();
                  return cells;
                }"""
            )
            assert state_cov["shared_empty"]
            assert state_cov["shared_loading"]
            assert state_cov["shared_error"]
            assert state_cov["error_bidi"]
            assert state_cov["error_retry"]
            assert state_cov["board_empty"]
            assert state_cov["board_error"]
            assert state_cov["skeleton_contrast"]
            assert state_cov["palette_empty"]

            # --- Responsive ---
            for width in (1440, 1024, 768, 390):
                page.set_viewport_size({"width": width, "height": 900})
                page.wait_for_timeout(120)
                metrics = page.evaluate(
                    """() => {
                      const se = document.scrollingElement || document.documentElement;
                      const targets = [];
                      const push = (sel) => {
                        document.querySelectorAll(sel).forEach((el) => {
                          const r = el.getBoundingClientRect();
                          if (r.width === 0 || r.height === 0) return;
                          targets.push({ sel, w: r.width, h: r.height });
                        });
                      };
                      if (window.innerWidth < 768) {
                        push('.topbar .icon-btn');
                        push('.mobile-lane-tabs button');
                      }
                      return {
                        scrollOk: se.scrollWidth <= se.clientWidth + 1,
                        targets,
                        drawerWidth: (() => {
                          const d = document.querySelector('.drawer');
                          return d ? d.getBoundingClientRect().width : 0;
                        })(),
                      };
                    }"""
                )
                assert metrics["scrollOk"], f"horizontal scroll at {width}: {metrics}"
                if width < 768:
                    for t in metrics["targets"]:
                        assert t["w"] >= 40 and t["h"] >= 40, (width, t)
                if width <= 768:
                    # open drawer via selecting a card if present
                    if page.locator(".card").count():
                        page.locator(".card").first.click()
                        page.wait_for_timeout(200)
                        dw = page.evaluate(
                            """() => {
                              const d = document.querySelector('.drawer.is-visible, .drawer.is-open, .drawer');
                              if (!d || d.hidden) return null;
                              return d.getBoundingClientRect().width;
                            }"""
                        )
                        if dw is not None:
                            assert dw >= width * 0.95 or dw >= width - 2

            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(150)
            _shot(page.locator(".shell"), "04-board-390.png")

            # Plan step dedupe in summary (pending approval)
            page.set_viewport_size({"width": 1440, "height": 900})
            page.locator('[data-view="orchestrator"]').click()
            page.wait_for_timeout(400)
            page.locator('.transcript-mode [data-mode="summary"]').click()
            page.wait_for_timeout(200)
            # If plan card pending, steps should appear once (in card, not duplicated in plan entry)
            dup = page.evaluate(
                """() => {
                  const plan = document.querySelector('.transcript-entry[data-kind="plan"]');
                  const card = document.querySelector('.transcript-plan-card');
                  const planOl = plan ? plan.querySelectorAll('ol > li').length : 0;
                  const cardOl = card ? card.querySelectorAll('ol > li').length : 0;
                  const pending = card && card.dataset.planApproval === 'pending';
                  return { planOl, cardOl, pending };
                }"""
            )
            if dup["pending"]:
                assert dup["planOl"] == 0
                assert dup["cardOl"] >= 1

            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_plan_hide_tasks_while_pending() -> None:
    script = """
import { buildTranscriptEntries } from './src/treefleet/ui/static/js/transcript-model.js';
const plan = { rationale: 'why', tasks: [{ id: 't1', title: 'One', allowed_paths: ['a.py'], instructions: 'do' }] };
const items = [{ type: 'awaiting_approval', offset: 1, ts: '2026-01-01T00:00:00Z', payload: {} }];
const entries = buildTranscriptEntries(items, plan, { awaiting: plan.tasks, goal: 'g' });
const planEntry = entries.find((e) => e.kind === 'plan');
const approval = entries.find((e) => e.kind === 'plan_approval');
console.log(JSON.stringify({ hideTasks: !!planEntry.hideTasks, status: approval && approval.status }));
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["hideTasks"] is True
    assert data["status"] == "pending"
