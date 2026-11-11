"""Phase 14.6: init records absolute interpreter paths; doctor names path/source/fix."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from treefleet.config import load_settings, resolve_venv_root
from treefleet.onboarding import (
    default_toml_text,
    resolve_init_venvs,
    run_doctor,
    run_init,
    venv_exists,
)

pytestmark = pytest.mark.phase11


def _fake_venv(root: Path) -> Path:
    if os.name == "nt":
        exe = root / "Scripts" / "python.exe"
    else:
        exe = root / "bin" / "python"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("", encoding="utf-8")
    return root


@pytest.fixture
def allow_long_wt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "treefleet.onboarding.worktree_budget",
        lambda root, *, limit=None: (True, 1, limit or 10_000),
    )


def test_init_records_absolute_paths_from_clone(
    tmp_path: Path, allow_long_wt: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = tmp_path / "clone"
    project = tmp_path / "project"
    wt = tmp_path / "wt"
    orch = _fake_venv(clone / ".venv-orch")
    worker = _fake_venv(clone / ".venv-worker")
    monkeypatch.setattr("treefleet.onboarding.discover_install_root", lambda: clone)

    code, notes = run_init(
        project,
        yes=True,
        worktree_root=str(wt),
        enable_pr=False,
        install_root=clone,
    )
    assert code == 0
    body = (project / "treefleet.toml").read_text(encoding="utf-8")
    assert orch.resolve().as_posix() in body
    assert worker.resolve().as_posix() in body
    assert "orchestrator_venv =" in body
    assert any(orch.resolve().as_posix() in note or str(orch.resolve()) in note for note in notes)
    joined = "\n".join(notes)
    assert "uv venv .venv-orch &&" not in joined


def test_precedence_install_root_over_project(tmp_path: Path) -> None:
    clone = tmp_path / "clone"
    project = tmp_path / "project"
    clone_orch = _fake_venv(clone / ".venv-orch")
    _fake_venv(project / ".venv-orch")
    clone_worker = _fake_venv(clone / ".venv-worker")
    _fake_venv(project / ".venv-worker")
    orch, worker = resolve_init_venvs(project, install_root=clone)
    assert orch == clone_orch.resolve()
    assert worker == clone_worker.resolve()


def test_precedence_falls_back_to_project(tmp_path: Path) -> None:
    project = tmp_path / "project"
    orch = _fake_venv(project / ".venv-orch")
    worker = _fake_venv(project / ".venv-worker")
    found_orch, found_worker = resolve_init_venvs(project, install_root=tmp_path / "missing")
    assert found_orch == orch.resolve()
    assert found_worker == worker.resolve()


def test_case3_omits_keys_and_prints_commands(
    tmp_path: Path, allow_long_wt: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    monkeypatch.setattr("treefleet.onboarding.discover_install_root", lambda: None)
    code, notes = run_init(
        project,
        yes=True,
        worktree_root=str(tmp_path / "wt"),
        enable_pr=False,
        install_root=None,
    )
    assert code == 0
    body = (project / "treefleet.toml").read_text(encoding="utf-8")
    assert "orchestrator_venv" not in body
    assert "worker_venv" not in body
    joined = "\n".join(notes)
    assert 'uv venv .venv-orch && uv pip install -p .venv-orch -e ".[orchestrator,dev]"' in joined
    assert 'uv venv .venv-worker && uv pip install -p .venv-worker -e ".[worker]"' in joined


def test_legacy_relative_config_still_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "legacy"
    folder.mkdir()
    orch = _fake_venv(folder / ".venv-orch")
    worker = _fake_venv(folder / ".venv-worker")
    (folder / "treefleet.toml").write_text(
        default_toml_text(windows=str(tmp_path / "wt"), other="/tmp/tf-wt"),
        encoding="utf-8",
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "phase14-interpreter-secret")
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    settings = load_settings(folder / "treefleet.toml")
    assert Path(settings.orchestrator_python) == Path(_python_path(orch))
    assert Path(settings.worker_python) == Path(_python_path(worker))
    assert settings.paths.orchestrator_venv == ".venv-orch"
    assert resolve_venv_root(folder, ".venv-orch", default=".venv-orch") == folder / ".venv-orch"


def test_doctor_red_names_path_source_and_fix(
    tmp_path: Path, allow_long_wt: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    monkeypatch.setattr("treefleet.onboarding.discover_install_root", lambda: None)
    assert (
        run_init(
            project,
            yes=True,
            worktree_root=str(tmp_path / "wt"),
            enable_pr=False,
            install_root=None,
        )[0]
        == 0
    )
    code, rows = run_doctor(
        cwd=project,
        env={},
        login=lambda _cli: (False, "not logged in"),
        models=lambda _cli: (False, "no models"),
        interpreter=lambda *_a, **_k: [],
    )
    assert code == 1
    text = "\n".join(line for _ok, line in rows)
    assert "orchestrator interpreter missing" in text
    assert "source: default" in text
    assert "fix: uv venv" in text and ".venv-orch" in text and ".[orchestrator,dev]" in text
    assert "worker interpreter missing" in text
    assert "DEEPSEEK_API_KEY is unset" in text


def test_doctor_toml_source_for_absolute_missing(tmp_path: Path, allow_long_wt: None) -> None:
    project = tmp_path / "project"
    project.mkdir()
    missing = (tmp_path / "clone" / ".venv-orch").resolve()
    (project / "treefleet.toml").write_text(
        default_toml_text(
            windows=str(tmp_path / "wt"),
            other="/tmp/tf-wt",
            orch=str(missing),
            worker=str((tmp_path / "clone" / ".venv-worker").resolve()),
        ),
        encoding="utf-8",
    )
    _code, rows = run_doctor(
        cwd=project,
        env={"DEEPSEEK_API_KEY": "x"},
        login=lambda _cli: (True, ""),
        models=lambda _cli: (True, ""),
        interpreter=lambda *_a, **_k: [],
    )
    text = "\n".join(line for _ok, line in rows)
    assert "source: toml" in text
    assert str(missing) in text or str(_python_path(missing)) in text


def test_install_reports_created_paths(tmp_path: Path, allow_long_wt: None, monkeypatch: pytest.MonkeyPatch) -> None:
    clone = tmp_path / "clone"
    clone.mkdir()
    project = tmp_path / "project"
    created: list[Path] = []

    def fake_install(folder: Path) -> tuple[bool, str]:
        created.append(folder)
        _fake_venv(folder / ".venv-orch")
        _fake_venv(folder / ".venv-worker")
        orch = (folder / ".venv-orch").resolve()
        worker = (folder / ".venv-worker").resolve()
        return True, f"install ok: created {orch} and {worker}"

    monkeypatch.setattr("treefleet.onboarding.discover_install_root", lambda: clone)
    code, notes = run_init(
        project,
        yes=True,
        worktree_root=str(tmp_path / "wt"),
        enable_pr=False,
        install=True,
        install_root=clone,
        run_install=fake_install,
    )
    assert code == 0
    assert created == [clone]
    joined = "\n".join(notes)
    assert "install ok: created" in joined
    assert str((clone / ".venv-orch").resolve()) in joined
    body = (project / "treefleet.toml").read_text(encoding="utf-8")
    assert (clone / ".venv-orch").resolve().as_posix() in body


def _python_path(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def test_venv_exists_helper(tmp_path: Path) -> None:
    root = tmp_path / ".venv-orch"
    assert venv_exists(root) is False
    _fake_venv(root)
    assert venv_exists(root) is True
