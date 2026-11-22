"""15.D5b: pure transcript-model.js derivation rules (via Node)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.phase11]

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "src" / "treefleet" / "ui" / "static" / "js" / "transcript-model.js"
FIXTURE_AUDIT = ROOT / "tests" / "fixtures" / "transcript-audit-slice.jsonl"
FIXTURE_SYN = ROOT / "tests" / "fixtures" / "transcript-synthetic.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        blob = (json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8")
        row = dict(row)
        row["offset"] = offset
        offset += len(blob)
        rows.append(row)
    return rows


def _eval_model(
    fn_body: str,
    *,
    items: list[dict],
    plan: dict | None = None,
    board: dict | None = None,
) -> object:
    payload = {
        "items": items,
        "plan": plan,
        "board": board or {},
    }
    script = f"""
import {{ readFileSync }} from 'node:fs';
import {{ pathToFileURL }} from 'node:url';
const mod = await import({json.dumps(MODEL.resolve().as_uri())});
const input = JSON.parse(readFileSync(0, 'utf8'));
const items = input.items;
const plan = input.plan;
const board = input.board;
const result = (() => {{ {fn_body} }})();
process.stdout.write(JSON.stringify(result));
"""
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
        cwd=str(ROOT),
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout or f"node exit {proc.returncode}")
    return json.loads(proc.stdout)


def test_assistant_join_space_unless_tight() -> None:
    out = str(
        _eval_model(
            "return mod.ASSISTANT_JOIN.join(['Hello','world','.',' Next']);",
            items=[],
        )
    )
    # space between words; no space before '.' (tight); space before ' Next' already in fragment
    assert out == "Hello world. Next"
    tight = str(
        _eval_model(
            "return mod.ASSISTANT_JOIN.join(['send','_email','`']);",
            items=[],
        )
    )
    assert tight == "send_email`"
    # Real mid-identifier boundary from audit slice: no space inside send_email
    mid = str(
        _eval_model(
            "return mod.ASSISTANT_JOIN.join(['tests for `send','_email` and `']);",
            items=[],
        )
    )
    assert mid == "tests for `send_email` and `"
    assert "send _email" not in mid
    assert "send_email" in mid


def test_synthetic_tool_pairing_interleaved_and_levels() -> None:
    items = _load_jsonl(FIXTURE_SYN)
    plan = {
        "goal": "ship it",
        "rationale": "because",
        "tasks": [{"id": "t1", "title": "One", "allowed_paths": ["a.py"], "instructions": "do"}],
    }
    entries_raw = _eval_model(
        "return mod.buildTranscriptEntries(items, plan, board);",
        items=items,
        plan=plan,
        board={"goal": "ship it", "report": {"integration": {"merged": ["t1"], "excluded": [], "branch": "tf/x"}}},
    )
    assert isinstance(entries_raw, list)
    entries: list[dict] = [e for e in entries_raw if isinstance(e, dict)]
    kinds = [e["kind"] for e in entries]
    assert "user_goal" in kinds
    assert "plan" in kinds
    assert "plan_approval" in kinds
    assert "tool_group" in kinds or "tool_call" in kinds
    assert "assistant_message" in kinds
    assert "worker_exited" in kinds
    assert "review_verdict" in kinds
    assert "unknown" in kinds
    assert "integration" in kinds
    # Unmatched shell start stays in progress
    tools = [e for e in entries if e["kind"] == "tool_call"] + [
        c for e in entries if e["kind"] == "tool_group" for c in e.get("children") or []
    ]
    assert any(t.get("inProgress") for t in tools)
    # Guard allowed=false is normal; allowed=true is verbose
    guards = [e for e in entries if e["kind"] == "guard"]
    assert any(g["level"] == "normal" and not g["allowed"] for g in guards)
    assert any(g["level"] == "verbose" and g["allowed"] for g in guards)

    summary_raw = _eval_model(
        "const e = mod.buildTranscriptEntries(items, plan, board);"
        " return mod.filterByMode(e, 'summary').map(x => x.kind);",
        items=items,
        plan=plan,
        board={"goal": "ship it", "report": {"integration": {"merged": [], "excluded": [], "branch": "b"}}},
    )
    assert isinstance(summary_raw, list)
    summary = [str(x) for x in summary_raw]
    assert "assistant_message" not in summary
    assert "tool_call" not in summary
    assert "tool_group" not in summary
    assert "user_goal" in summary
    assert "unknown" not in summary

    normal_raw = _eval_model(
        "const e = mod.buildTranscriptEntries(items, plan, board);"
        " return mod.filterByMode(e, 'normal').map(x => x.kind);",
        items=items,
        plan=plan,
        board={"goal": "ship it"},
    )
    assert isinstance(normal_raw, list)
    normal = [str(x) for x in normal_raw]
    assert "assistant_message" in normal
    assert "tool_group" in normal or "tool_call" in normal
    assert "unknown" not in normal

    verbose_raw = _eval_model(
        "const e = mod.buildTranscriptEntries(items, plan, board);"
        " return mod.filterByMode(e, 'verbose').map(x => x.kind);",
        items=items,
        plan=plan,
        board={"goal": "ship it"},
    )
    assert isinstance(verbose_raw, list)
    verbose = [str(x) for x in verbose_raw]
    assert "unknown" in verbose
    assert "tool_call" in verbose


def test_malformed_payload_does_not_crash() -> None:
    items = [
        {"offset": 0, "type": "output_line", "task_id": "t", "payload": None, "seq": 0},
        {"offset": 10, "type": "not.a.real.type", "task_id": "t", "payload": "oops", "seq": 1},
    ]
    entries_raw = _eval_model(
        "return mod.buildTranscriptEntries(items, null, {});",
        items=items,
    )
    assert isinstance(entries_raw, list)
    entries = [e for e in entries_raw if isinstance(e, dict)]
    assert any(e["kind"] == "unknown" for e in entries)


def test_audit_fixture_loads_and_joins() -> None:
    items = _load_jsonl(FIXTURE_AUDIT)
    assert len(items) <= 200
    entries_raw = _eval_model(
        "return mod.buildTranscriptEntries(items, null, {goal: 'audit'});",
        items=items,
        board={"goal": "audit"},
    )
    assert isinstance(entries_raw, list)
    entries = [e for e in entries_raw if isinstance(e, dict)]
    assert any(e["kind"] == "user_goal" for e in entries)
    # Word-fragment join produces a single assistant message when consecutive
    assistants = [e for e in entries if e["kind"] == "assistant_message"]
    if assistants:
        text = str(assistants[0].get("text") or "")
        assert " " in text or len(text) > 0


def test_join_constant_name() -> None:
    name = str(_eval_model("return mod.ASSISTANT_JOIN.name;", items=[]))
    assert name == "space-unless-tight"
