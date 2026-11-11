"""Phase 14.1: init, doctor, safe messages. tmp dirs only."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from treefleet.config import ConfigError, config_path, load_settings
from treefleet.demo import preflight
from treefleet.git.worktrees import worktree_budget
from treefleet.onboarding import (
    default_branch,
    default_toml_text,
    doctor_main,
    init_main,
    run_doctor,
    run_init,
    validate_local_repo,
)

pytestmark = pytest.mark.phase11

SECRET = "sk-phase14-secret-value-do-not-leak"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_clone_venv_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep legacy onboarding tests on relative/default path behavior."""
    monkeypatch.setattr("treefleet.onboarding.discover_install_root", lambda: None)


@pytest.fixture
def allow_long_wt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pytest tmp roots often exceed the real 120-char budget."""
    monkeypatch.setattr(
        "treefleet.onboarding.worktree_budget",
        lambda root, *, limit=None: (True, 1, limit or 10_000),
    )


def _git(repo: Path, *, branch: str = "main") -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", branch], cwd=repo, check=True, shell=False, capture_output=True)
    subprocess.run(["git", "config", "user.email", "treefleet@local"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "config", "user.name", "TreeFleet"], cwd=repo, check=True, shell=False)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=repo, check=True, shell=False)
    (repo / "README.md").write_text("base\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, shell=False, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, shell=False, capture_output=True)


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    ).stdout.strip()


def _branch(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    ).stdout.strip()


def test_init_fresh_and_idempotent(tmp_path: Path, allow_long_wt: None) -> None:
    folder = tmp_path / "tf"
    wt = tmp_path / "wt"
    code, notes = run_init(folder, yes=True, worktree_root=str(wt), enable_pr=False)
    assert code == 0
    assert (folder / "treefleet.toml").is_file()
    assert (folder / ".env.example").is_file()
    example = (folder / ".env.example").read_text(encoding="utf-8")
    assert "DEEPSEEK_API_KEY=" in example
    assert "TYPESAFE_API_KEY=" in example
    assert "Optional. Jev" in example or "optional" in example.lower()
    assert "CURSOR_API_KEY=" in example
    assert "LANGFUSE_PUBLIC_KEY=" in example
    assert SECRET not in example
    for line in example.splitlines():
        if line.strip() and not line.strip().startswith("#") and "=" in line:
            assert line.split("=", 1)[1] == ""
    assert any("TYPESAFE_API_KEY is optional" in note or "optional (Jev)" in note for note in notes)
    body = (folder / "treefleet.toml").read_text(encoding="utf-8")
    assert "[pr]" not in body
    assert "[models]" in body and "[paths]" in body and "[pricing]" in body
    assert any("uv venv .venv-orch" in note for note in notes)
    first = body
    code2, notes2 = run_init(folder, yes=True, worktree_root=str(wt))
    assert code2 == 0
    assert "already initialized" in "\n".join(notes2)
    assert (folder / "treefleet.toml").read_text(encoding="utf-8") == first


def test_init_refuses_foreign_project_without_yes(tmp_path: Path, allow_long_wt: None) -> None:
    folder = tmp_path / "foreign"
    folder.mkdir()
    (folder / "README.md").write_text("other\n", encoding="utf-8")
    code, notes = run_init(
        folder,
        yes=False,
        worktree_root=str(tmp_path / "wt"),
        ask=lambda _prompt: "n",
    )
    assert code == 1
    assert notes == ["write refused"]
    assert not (folder / "treefleet.toml").exists()


def test_init_gitignore_append(tmp_path: Path, allow_long_wt: None) -> None:
    folder = tmp_path / "tf"
    _git(folder)
    (folder / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    code, notes = run_init(folder, yes=True, worktree_root=str(tmp_path / "wt"), enable_pr=False)
    assert code == 0
    text = (folder / ".gitignore").read_text(encoding="utf-8")
    assert "*.pyc" in text
    assert ".env" in text.splitlines()
    assert any("appended .env" in note for note in notes)


def test_init_rejects_long_worktree_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "tf"
    long_root = tmp_path / ("w" * 80)
    monkeypatch.setattr(
        "treefleet.onboarding.worktree_budget",
        lambda root, *, limit=None: worktree_budget(root, limit=40),
    )
    code, notes = run_init(folder, yes=True, worktree_root=str(long_root), enable_pr=False)
    assert code == 1
    assert any("shorter root" in note or "limit" in note for note in notes)
    assert not (folder / "treefleet.toml").exists()
    assert worktree_budget(long_root, limit=40)[0] is False


def test_init_yes_skips_repo_question(tmp_path: Path, allow_long_wt: None) -> None:
    folder = tmp_path / "tf"

    def boom(_prompt: str) -> str:
        raise AssertionError("ask should not run with --yes")

    code, _notes = run_init(
        folder,
        yes=True,
        worktree_root=str(tmp_path / "wt"),
        enable_pr=False,
        ask=boom,
    )
    assert code == 0


def test_init_local_repo_valid_and_unchanged(tmp_path: Path, allow_long_wt: None) -> None:
    folder = tmp_path / "tf"
    target = tmp_path / "app"
    _git(target, branch="develop")
    before = _head(target)
    status_before = subprocess.run(
        ["git", "-C", str(target), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    ).stdout
    code, notes = run_init(
        folder,
        yes=True,
        repo=str(target),
        worktree_root=str(tmp_path / "wt"),
        enable_pr=False,
    )
    assert code == 0
    assert _head(target) == before
    assert _branch(target) == "develop"
    assert (
        subprocess.run(
            ["git", "-C", str(target), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            shell=False,
        ).stdout
        == status_before
    )
    assert any(str(target.resolve()) in note for note in notes)


def test_init_rejects_invalid_and_empty_repo(tmp_path: Path, allow_long_wt: None) -> None:
    folder = tmp_path / "tf"
    missing = tmp_path / "missing"
    code, notes = run_init(
        folder,
        yes=True,
        repo=str(missing),
        worktree_root=str(tmp_path / "wt"),
        enable_pr=False,
    )
    assert code == 1
    assert any("does not exist" in note for note in notes)
    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=empty, check=True, shell=False, capture_output=True)
    assert validate_local_repo(empty) is not None
    code2, notes2 = run_init(
        folder,
        yes=True,
        repo=str(empty),
        worktree_root=str(tmp_path / "wt"),
        enable_pr=False,
    )
    assert code2 == 1
    assert any("no commits" in note for note in notes2)


def test_default_branch_detection(tmp_path: Path) -> None:
    for name in ("main", "master", "trunk"):
        repo = tmp_path / name
        _git(repo, branch=name)
        assert default_branch(repo) == name


def test_init_pr_mode_uses_gh_login(tmp_path: Path, allow_long_wt: None) -> None:
    folder = tmp_path / "tf"
    target = tmp_path / "app"
    _git(target, branch="main")
    subprocess.run(
        ["git", "-C", str(target), "remote", "add", "origin", "https://github.com/acme/widgets.git"],
        check=True,
        shell=False,
        capture_output=True,
    )

    def gh(args: list[str]) -> tuple[int, str, str]:
        if args[:1] == ["auth"]:
            return 0, "ok\n", ""
        if args[:3] == ["api", "user", "--jq"]:
            return 0, "ada\n", ""
        return 1, "", "no"

    code, notes = run_init(
        folder,
        yes=True,
        repo=str(target),
        pr_repo="acme/widgets",
        enable_pr=True,
        worktree_root=str(tmp_path / "wt"),
        gh=gh,
    )
    assert code == 0
    body = (folder / "treefleet.toml").read_text(encoding="utf-8")
    assert "[pr]" in body
    assert "acme/widgets" in body
    assert "base = " in body and "main" in body
    assert 'trusted_commenters = ["ada"]' in body
    assert "AmartyaKumar11" not in body
    assert "demo-rehearsal" not in body
    assert any("PR mode on" in note for note in notes)


def test_load_settings_missing_toml_is_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="treefleet init"):
        load_settings(tmp_path / "treefleet.toml")


def test_treefleet_config_isolates_pr_off(
    tmp_path: Path,
    hermetic_treefleet_config: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "present")
    monkeypatch.setenv("TYPESAFE_API_KEY", "present")
    dirty = tmp_path / "dirty.toml"
    dirty.write_text(
        default_toml_text(windows=r"C:\wt", other="/tmp/tf-wt")
        + '\n[pr]\nenabled = true\nrepo = "fake/other"\nbase = "main"\n',
        encoding="utf-8",
    )
    assert Path(os.environ["TREEFLEET_CONFIG"]) == hermetic_treefleet_config
    assert config_path() == hermetic_treefleet_config
    settings = load_settings()
    assert settings.pr.enabled is False
    monkeypatch.setenv("TREEFLEET_CONFIG", str(dirty))
    leaked = load_settings()
    assert leaked.pr.enabled is True
    assert leaked.pr.repo == "fake/other"


def test_doctor_all_green_with_fakes(tmp_path: Path, allow_long_wt: None) -> None:
    folder = tmp_path / "tf"
    wt = tmp_path / "wt"
    assert run_init(folder, yes=True, worktree_root=str(wt), enable_pr=False, install_root=None)[0] == 0
    orch_venv = ROOT / ".venv-orch"
    worker_venv = ROOT / ".venv-worker"
    text = (folder / "treefleet.toml").read_text(encoding="utf-8")
    if "orchestrator_venv" not in text:
        text = text.replace(
            "[paths]\n",
            "[paths]\n"
            f"orchestrator_venv = {str(orch_venv.resolve())!r}\n"
            f"worker_venv = {str(worker_venv.resolve())!r}\n",
            1,
        )
    else:
        text = text.replace(".venv-orch", str(orch_venv.resolve()))
        text = text.replace(".venv-worker", str(worker_venv.resolve()))
    (folder / "treefleet.toml").write_text(text, encoding="utf-8")

    code, rows = run_doctor(
        cwd=folder,
        env={"DEEPSEEK_API_KEY": "present", "TYPESAFE_API_KEY": "present"},
        login=lambda _cli: (True, ""),
        models=lambda _cli: (True, ""),
        interpreter=lambda _repo, _py: [
            (True, "green  worker interpreter imports crewai (fake)"),
            (True, "green  check interpreter imports pytest (fake)"),
        ],
    )
    printed = "\n".join(text for _ok, text in rows)
    assert code == 0, printed
    assert "treefleet.toml found" in printed
    assert "worktree root ok" in printed
    assert "demo reset" not in printed
    assert SECRET not in printed


def test_doctor_red_paths_and_dirty_warning(tmp_path: Path, allow_long_wt: None) -> None:
    folder = tmp_path / "empty"
    folder.mkdir()
    code, rows = run_doctor(
        cwd=folder,
        env={},
        login=lambda _cli: (False, "cursor-agent login"),
        models=lambda _cli: (False, "cursor-agent --list-models"),
    )
    assert code == 1
    text = "\n".join(line for _ok, line in rows)
    assert "treefleet.toml not found" in text or "Run treefleet init" in text

    app = tmp_path / "app"
    _git(app)
    (app / "dirty.txt").write_text("x\n", encoding="utf-8")
    tf = tmp_path / "tf"
    assert run_init(tf, yes=True, worktree_root=str(tmp_path / "wt"), enable_pr=False)[0] == 0
    code2, rows2 = run_doctor(
        cwd=tf,
        repo=app,
        env={"DEEPSEEK_API_KEY": SECRET, "TYPESAFE_API_KEY": SECRET},
        login=lambda _cli: (True, ""),
        models=lambda _cli: (True, ""),
        interpreter=lambda _repo, _py: [
            (
                False,
                "red    worker interpreter cannot import crewai (x)\n"
                '       fix: uv pip install -p .venv-worker -e ".[worker]"',
            ),
            (
                False,
                "red    no check interpreter can import pytest\n"
                '       fix: uv pip install -p .venv-orch -e ".[orchestrator,dev]"',
            ),
        ],
    )
    assert code2 == 1
    joined = "\n".join(line for _ok, line in rows2)
    assert SECRET not in joined
    assert "yellow working tree is dirty" in joined
    assert "demo reset" not in joined
    assert ".[worker]" in joined
    assert any(not ok for ok, _line in rows2)


def test_doctor_json_hides_secrets(tmp_path: Path, allow_long_wt: None, capsys: pytest.CaptureFixture[str]) -> None:
    tf = tmp_path / "tf"
    assert run_init(tf, yes=True, worktree_root=str(tmp_path / "wt"), enable_pr=False)[0] == 0
    code, _rows = run_doctor(
        cwd=tf,
        env={"DEEPSEEK_API_KEY": SECRET, "TYPESAFE_API_KEY": SECRET},
        login=lambda _cli: (True, ""),
        models=lambda _cli: (True, ""),
        interpreter=lambda *_a, **_k: [],
        as_json=True,
    )
    out = capsys.readouterr().out
    assert SECRET not in out
    payload = json.loads(out)
    assert isinstance(payload, list)
    assert all("ok" in row and "text" in row for row in payload)
    assert code in {0, 1}


def test_doctor_cli_bad_args() -> None:
    assert doctor_main(["--bogus"]) == 2


def test_init_cli_bad_args() -> None:
    assert init_main(["--enable-pr", "--no-pr"]) == 2


def test_pr_enabled_requires_explicit_base(tmp_path: Path, allow_long_wt: None) -> None:
    from treefleet.pr.config import PrConfigError, load_pr

    with pytest.raises(PrConfigError, match=r"set \[pr\]\.base"):
        load_pr({"enabled": True, "repo": "acme/widgets"})
    with pytest.raises(PrConfigError, match=r"set \[pr\]\.base"):
        load_pr({"enabled": True, "repo": "acme/widgets", "base": "  "})
    off = load_pr({"enabled": False})
    assert off.enabled is False
    assert off.base == ""
    ok = load_pr({"enabled": True, "repo": "acme/widgets", "base": "main"})
    assert ok.base == "main"

    folder = tmp_path / "tf"
    wt = tmp_path / "wt"
    assert run_init(folder, yes=True, worktree_root=str(wt), enable_pr=False)[0] == 0
    body = (folder / "treefleet.toml").read_text(encoding="utf-8")
    assert 'base = "demo-base"' not in body
    assert "[pr]" not in body
    (folder / "treefleet.toml").write_text(
        body + '\n[pr]\nenabled = true\nrepo = "acme/widgets"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match=r"set \[pr\]\.base"):
        load_settings(folder / "treefleet.toml")


def test_preflight_messages_never_suggest_demo_reset(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git(repo, branch="other")
    (repo / "extra.txt").write_text("x\n", encoding="utf-8")
    rows = preflight(
        repo,
        env={"DEEPSEEK_API_KEY": "x", "TYPESAFE_API_KEY": "x"},
        login=lambda _cli: (True, ""),
        models=lambda _cli: (True, ""),
        auth=lambda _env: [],
    )
    printed = "\n".join(text for _ok, text in rows)
    assert "demo reset" not in printed
    assert "nothing is deleted automatically" in printed
    assert any(ok and "yellow working tree is dirty" in text for ok, text in rows)
