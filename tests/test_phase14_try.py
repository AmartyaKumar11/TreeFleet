"""Phase 14.2: treefleet try + confirmation screen. tmp + fakes only."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from treefleet.config import load_settings
from treefleet.confirm import SAFE_SENTENCE, confirm_lines, confirm_run
from treefleet.orchestrator.pipeline import RunOutcome
from treefleet.pr.config import default_pr
from treefleet.sample.create import (
    DEFAULT_GOAL,
    MARKER,
    SAMPLE_TARGETS,
    create_sample,
    ensure_sample,
    targets_disjoint,
)
from treefleet.try_cmd import run_try

pytestmark = pytest.mark.phase11

FORBIDDEN = ("pr-sentinel-demo", "AmartyaKumar11")


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        shell=False,
        check=True,
    )
    return proc.stdout


def test_sample_targets_are_disjoint() -> None:
    assert targets_disjoint()
    seen: set[str] = set()
    for paths in SAMPLE_TARGETS.values():
        for path in paths:
            assert path not in seen
            seen.add(path)
    assert "tfsample/mathops.py" in seen
    assert "tfsample/greeter.py" in seen
    assert "docs/greeter.md" in seen


def test_create_sample_files_commit_and_local_identity(tmp_path: Path) -> None:
    dest = tmp_path / "sample"
    notes = create_sample(dest)
    assert (dest / MARKER).is_file()
    assert (dest / "tfsample" / "mathops.py").is_file()
    assert (dest / "tfsample" / "greeter.py").is_file()
    assert (dest / "tests" / "test_mathops.py").is_file()
    assert (dest / "tests" / "test_greeter.py").is_file()
    assert _git(dest, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert _git(dest, "rev-list", "--count", "HEAD").strip() == "1"
    assert _git(dest, "config", "--local", "user.email").strip() == "treefleet-sample@local"
    global_email = subprocess.run(
        ["git", "config", "--global", "user.email"],
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )
    assert "treefleet-sample@local" not in (global_email.stdout or "")
    blob = "\n".join(notes) + "\n" + (dest / "README.md").read_text(encoding="utf-8")
    for bad in FORBIDDEN:
        assert bad not in blob
    # templates themselves
    for path in dest.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            for bad in FORBIDDEN:
                assert bad not in text, path


def test_ensure_sample_refuses_nonempty_foreign(tmp_path: Path) -> None:
    dest = tmp_path / "taken"
    dest.mkdir()
    (dest / "other.txt").write_text("nope\n", encoding="utf-8")
    code, notes = ensure_sample(dest)
    assert code == 1
    assert any("refusing to overwrite" in note for note in notes)


def test_ensure_sample_reuses_clean_marker(tmp_path: Path) -> None:
    dest = tmp_path / "sample"
    create_sample(dest)
    head = _git(dest, "rev-parse", "HEAD").strip()
    code, notes = ensure_sample(dest)
    assert code == 0
    assert any("reusing" in note for note in notes)
    assert _git(dest, "rev-parse", "HEAD").strip() == head


def test_ensure_sample_refuses_dirty_reuse(tmp_path: Path) -> None:
    dest = tmp_path / "sample"
    create_sample(dest)
    (dest / "extra.txt").write_text("dirty\n", encoding="utf-8")
    code, notes = ensure_sample(dest)
    assert code == 1
    assert any("dirty" in note for note in notes)


def test_confirmation_lists_facts_and_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "present")
    monkeypatch.setenv("TYPESAFE_API_KEY", "present")
    dest = tmp_path / "sample"
    create_sample(dest)
    settings = load_settings()
    lines = confirm_lines(dest, settings)
    text = "\n".join(lines)
    assert str(dest.resolve()) in text
    assert "base branch:" in text
    assert "working tree: clean" in text
    assert "PR mode:" in text
    assert "worktree root:" in text
    assert "agent CLI:" in text
    assert "model tiers:" in text
    assert "budgets:" in text
    assert SAFE_SENTENCE in text
    for bad in FORBIDDEN:
        assert bad not in text
    code, out = confirm_run(dest, settings, yes=True)
    assert code == 0
    assert any("proceeding" in line for line in out)


def test_confirmation_refuses_noninteractive_without_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "present")
    monkeypatch.setenv("TYPESAFE_API_KEY", "present")
    dest = tmp_path / "sample"
    create_sample(dest)
    settings = load_settings()
    code, out = confirm_run(dest, settings, yes=False, interactive=False)
    assert code == 2
    assert any("non-interactive" in line for line in out)


def test_confirmation_dirty_is_warning_not_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "present")
    monkeypatch.setenv("TYPESAFE_API_KEY", "present")
    dest = tmp_path / "sample"
    create_sample(dest)
    (dest / "dirt.txt").write_text("x\n", encoding="utf-8")
    settings = load_settings()
    lines = "\n".join(confirm_lines(dest, settings))
    assert "dirty (warning)" in lines
    code, _out = confirm_run(dest, settings, yes=True)
    assert code == 0


def test_try_doctor_red_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "present")
    monkeypatch.setenv("TYPESAFE_API_KEY", "present")
    dest = tmp_path / "sample"
    called = {"pipe": 0}

    def doctor(**_kwargs):  # type: ignore[no-untyped-def]
        return 1, [(False, "red    fake doctor failure\n       fix: none")]

    def pipeline(*_a, **_k):  # type: ignore[no-untyped-def]
        called["pipe"] += 1
        return RunOutcome(0, None, {}, "deadbeef")

    code, notes = run_try(
        dest=dest,
        yes=True,
        doctor=doctor,
        pipeline=pipeline,
        settings=load_settings(),
    )
    assert code == 1
    assert called["pipe"] == 0
    assert any("doctor red" in note for note in notes)


def test_try_green_forces_pr_off_and_keeps_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "present")
    monkeypatch.setenv("TYPESAFE_API_KEY", "present")
    dest = tmp_path / "sample"
    create_sample(dest)
    head_before = _git(dest, "rev-parse", "HEAD").strip()
    settings = load_settings().model_copy(
        update={"pr": default_pr().model_copy(update={"enabled": True, "repo": "fake/other", "base": "main"})}
    )
    seen: dict[str, object] = {}

    def doctor(**_kwargs):  # type: ignore[no-untyped-def]
        return 0, [(True, "green  fake doctor")]

    def pipeline(request, sett, **_kwargs):  # type: ignore[no-untyped-def]
        seen["pr_enabled"] = sett.pr.enabled
        seen["goal"] = request.goal
        seen["repo"] = request.repo
        return RunOutcome(0, None, {}, "abcd1234")

    code, notes = run_try(
        dest=dest,
        yes=True,
        doctor=doctor,
        pipeline=pipeline,
        settings=settings,
    )
    assert code == 0
    assert seen["pr_enabled"] is False
    assert seen["repo"] == str(dest)
    assert seen["goal"] == DEFAULT_GOAL
    assert _git(dest, "rev-parse", "HEAD").strip() == head_before
    blob = "\n".join(notes)
    for bad in FORBIDDEN:
        assert bad not in blob
    assert any("did not merge into main" in note for note in notes)
