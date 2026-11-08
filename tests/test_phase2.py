"""Phase 2: worktrees, canary, isolation proofs, merge, and read-only gitops tools."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import psutil
import pytest
from mcp.types import CallToolResult

from treefleet.git.canary import write_canary
from treefleet.git.merge import integrate
from treefleet.git.proofs import (
    collect_changes,
    revert_out_of_scope,
    snapshot_main,
    snapshot_siblings,
    verify_main,
    verify_siblings,
    verify_task_isolation,
)
from treefleet.git.worktrees import GitError, create_worktree, list_worktrees, prepare_repo, remove_worktree
from treefleet.mcp_servers.gitops_server import READ_ONLY_TOOLS, ToolResult, build_gitops_server

pytestmark = pytest.mark.phase2

README = "alpha\nmiddle\nbeta\n"


def _short_root() -> Path:
    path = Path(r"C:\tf") if sys.platform == "win32" else Path("/tmp/tf")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        shell=False,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr.strip() or proc.stdout.strip() or "git failed")
    return proc.stdout


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    _git(["init", "-b", "main"], path)
    _git(["config", "user.email", "treefleet@local"], path)
    _git(["config", "user.name", "TreeFleet"], path)
    _git(["config", "core.autocrlf", "false"], path)
    (path / "README.md").write_text(README, encoding="utf-8", newline="\n")
    (path / "notes.md").write_text("notes\n", encoding="utf-8", newline="\n")
    _git(["add", "README.md", "notes.md"], path)
    _git(["commit", "-m", "init"], path)


def _commit(worktree: Path, rel: str, text: str) -> None:
    (worktree / rel).write_text(text, encoding="utf-8", newline="\n")
    _git(["add", rel], worktree)
    _git(["commit", "-m", f"edit {rel}"], worktree)


@pytest.fixture
def git_repo(tmp_path: Path) -> Iterator[tuple[Path, str, list[Path]]]:
    repo = tmp_path / "repo"
    _init_repo(repo)
    prepare_repo(repo)
    run_id = uuid.uuid4().hex[:8]
    made: list[Path] = []
    try:
        yield repo, run_id, made
    finally:
        for path in reversed(made):
            try:
                remove_worktree(repo, path)
            except GitError:
                pass
        shutil.rmtree(_short_root() / run_id, ignore_errors=True)


def _add(repo: Path, run_id: str, task: str, made: list[Path]) -> Path:
    path = create_worktree(repo, run_id, task, _short_root())
    made.append(path)
    return path


def test_three_worktrees_have_distinct_toplevels_and_branches(git_repo: tuple[Path, str, list[Path]]) -> None:
    repo, run_id, made = git_repo
    seen: list[tuple[str, str]] = []
    for task in ("alpha", "beta", "gamma"):
        worktree = _add(repo, run_id, task, made)
        assert len(str(worktree)) < 120
        top = _git(["rev-parse", "--show-toplevel"], worktree).strip()
        branch = _git(["branch", "--show-current"], worktree).strip()
        seen.append((str(Path(top).resolve()), branch))
    assert len({top for top, _ in seen}) == 3
    assert len({branch for _, branch in seen}) == 3
    assert {branch for _, branch in seen} == {f"tf/{run_id}/{task}" for task in ("alpha", "beta", "gamma")}
    listed = {(item.path.resolve(), item.branch) for item in list_worktrees(repo)}
    for top, branch in seen:
        assert (Path(top).resolve(), branch) in listed


def test_long_worktree_path_is_rejected(git_repo: tuple[Path, str, list[Path]]) -> None:
    repo, run_id, _made = git_repo
    with pytest.raises(GitError):
        create_worktree(repo, run_id, "alpha", repo.parent / ("x" * 120))


def test_canary_in_worktree_fails_proof(git_repo: tuple[Path, str, list[Path]]) -> None:
    repo, run_id, made = git_repo
    worktree = _add(repo, run_id, "alpha", made)
    content = write_canary(repo, run_id)
    assert content.startswith(f"canary-{run_id}-")
    exclude = _git(["rev-parse", "--git-common-dir"], repo).strip()
    exclude_path = Path(exclude)
    if not exclude_path.is_absolute():
        exclude_path = (repo / exclude_path).resolve()
    assert ".treefleet_canary" in (exclude_path / "info" / "exclude").read_text(encoding="utf-8")
    clean = verify_task_isolation(
        repo,
        task_id="alpha",
        worktree=worktree,
        run_id=run_id,
        base_ref="main",
        allowed_paths=["README.md"],
    )
    assert clean.canary_absent and clean.ok
    shutil.copyfile(repo / ".treefleet_canary", worktree / ".treefleet_canary")
    proof = verify_task_isolation(
        repo,
        task_id="alpha",
        worktree=worktree,
        run_id=run_id,
        base_ref="main",
        allowed_paths=["README.md"],
    )
    assert proof.canary_absent is False
    assert proof.ok is False


def test_write_outside_allowed_paths_fails_proof(git_repo: tuple[Path, str, list[Path]]) -> None:
    repo, run_id, made = git_repo
    worktree = _add(repo, run_id, "alpha", made)
    write_canary(repo, run_id)
    (worktree / "sneak.txt").write_text("outside\n", encoding="utf-8")
    proof = verify_task_isolation(
        repo,
        task_id="alpha",
        worktree=worktree,
        run_id=run_id,
        base_ref="main",
        allowed_paths=["README.md"],
        denied_paths=["secret.txt"],
    )
    assert proof.changes_within_allowed_paths is False
    assert "sneak.txt" in proof.offending_paths
    assert proof.ok is False


def test_missing_worktree_fails_proof(git_repo: tuple[Path, str, list[Path]]) -> None:
    repo, run_id, made = git_repo
    worktree = _add(repo, run_id, "alpha", made)
    remove_worktree(repo, worktree)
    proof = verify_task_isolation(
        repo,
        task_id="alpha",
        worktree=worktree,
        run_id=run_id,
        base_ref="main",
        allowed_paths=["README.md"],
    )
    assert proof.registered_in_worktree_list is False
    assert proof.ok is False


def test_main_and_sibling_writes_fail_proofs(git_repo: tuple[Path, str, list[Path]]) -> None:
    repo, run_id, made = git_repo
    write_canary(repo, run_id)
    owner = _add(repo, run_id, "alpha", made)
    sibling = _add(repo, run_id, "beta", made)
    main_before = snapshot_main(repo)
    siblings_before = snapshot_siblings(repo)
    assert verify_main(repo, main_before).ok
    assert verify_siblings(siblings_before, snapshot_siblings(repo), owned={owner}).ok
    (repo / "README.md").write_text("main checkout write\n", encoding="utf-8")
    (sibling / "sneak.txt").write_text("sibling write\n", encoding="utf-8")
    main_proof = verify_main(repo, main_before)
    sibling_proof = verify_siblings(siblings_before, snapshot_siblings(repo), owned={owner})
    assert main_proof.canary_present
    assert main_proof.status_before != main_proof.status_after
    assert main_proof.ok is False
    assert sibling_proof.ok is False
    assert any(Path(path).resolve() == sibling.resolve() for path in sibling_proof.offending)


def test_launcher_cwd_follows_the_real_interpreter(git_repo: tuple[Path, str, list[Path]]) -> None:
    repo, run_id, made = git_repo
    worktree = _add(repo, run_id, "alpha", made)
    write_canary(repo, run_id)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=worktree,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        target = os.path.normcase(str(worktree.resolve()))
        interpreter: int | None = None
        deadline = time.monotonic() + 5
        while interpreter is None and time.monotonic() < deadline:
            try:
                root = psutil.Process(proc.pid)
                children = root.children(recursive=True)
            except psutil.Error:
                time.sleep(0.05)
                continue
            for item in children:
                try:
                    if "python" not in item.name().lower():
                        continue
                    cwd = os.path.normcase(str(Path(item.cwd()).resolve()))
                except (psutil.Error, OSError):
                    continue
                if cwd == target:
                    interpreter = int(item.pid)
                    break
            else:
                time.sleep(0.05)
        if interpreter is None:
            root = psutil.Process(proc.pid)
            own = os.path.normcase(str(Path(root.cwd()).resolve()))
            assert own == target
            interpreter = int(root.pid)
        proof = verify_task_isolation(
            repo,
            task_id="alpha",
            worktree=worktree,
            run_id=run_id,
            base_ref="main",
            allowed_paths=["README.md"],
            pid=interpreter,
            reported_cwd=str(worktree.resolve()),
            ready_at=time.monotonic(),
        )
        if sys.platform == "win32":
            launcher_cwd = Path(psutil.Process(proc.pid).cwd()).resolve()
            assert launcher_cwd != worktree.resolve()
        assert proof.process_cwd_matches, [item.model_dump(mode="json") for item in proof.cwd_attempts]
        assert proof.toplevel_matches
        assert proof.registered_in_worktree_list
        assert proof.ok
    finally:
        remove_worktree(repo, worktree, pid=proc.pid)
        assert not psutil.pid_exists(proc.pid)


def test_reported_cwd_mismatch_fails_and_keeps_the_attempt(git_repo: tuple[Path, str, list[Path]]) -> None:
    repo, run_id, made = git_repo
    worktree = _add(repo, run_id, "alpha", made)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=worktree,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        proof = verify_task_isolation(
            repo,
            task_id="alpha",
            worktree=worktree,
            run_id=run_id,
            base_ref="main",
            allowed_paths=["README.md"],
            pid=proc.pid,
            reported_cwd=str(repo / "not-the-worktree"),
            ready_at=time.monotonic(),
        )
        assert proof.process_cwd_matches is False
        assert proof.cwd_attempts
        assert proof.cwd_attempts[0].pid == proc.pid
    finally:
        proc.kill()
        proc.wait(timeout=5)


def test_unresolved_interpreter_fails_the_cwd_check(git_repo: tuple[Path, str, list[Path]]) -> None:
    repo, run_id, made = git_repo
    worktree = _add(repo, run_id, "alpha", made)
    proof = verify_task_isolation(
        repo,
        task_id="alpha",
        worktree=worktree,
        run_id=run_id,
        base_ref="main",
        allowed_paths=["README.md"],
        cwd_required=True,
    )
    assert proof.process_cwd_matches is False
    assert proof.cwd_attempts[0].error == "unresolved"


def test_dead_pid_is_inconclusive_then_fails(git_repo: tuple[Path, str, list[Path]]) -> None:
    repo, run_id, made = git_repo
    worktree = _add(repo, run_id, "alpha", made)
    proof = verify_task_isolation(
        repo,
        task_id="alpha",
        worktree=worktree,
        run_id=run_id,
        base_ref="main",
        allowed_paths=["README.md"],
        pid=2**31 - 1,
        reported_cwd=str(worktree.resolve()),
        ready_at=time.monotonic() - 30,
    )
    assert proof.process_cwd_matches is False
    assert proof.cwd_attempts
    assert proof.cwd_attempts[-1].processes[0].error == "NoSuchProcess"


def test_collect_changes_includes_tracked_staged_and_untracked(git_repo: tuple[Path, str, list[Path]]) -> None:
    repo, run_id, made = git_repo
    worktree = _add(repo, run_id, "alpha", made)
    _commit(worktree, "README.md", "committed\n")
    (worktree / "README.md").write_text("unstaged\n", encoding="utf-8")
    (worktree / "notes.md").write_text("staged\n", encoding="utf-8")
    _git(["add", "notes.md"], worktree)
    (worktree / "extra.txt").write_text("untracked\n", encoding="utf-8")
    assert collect_changes(worktree, "main") == ["README.md", "extra.txt", "notes.md"]


def test_revert_out_of_scope_restores_tracked_and_removes_untracked(
    git_repo: tuple[Path, str, list[Path]],
) -> None:
    repo, run_id, made = git_repo
    worktree = _add(repo, run_id, "alpha", made)
    (worktree / "README.md").write_text("kept\n", encoding="utf-8")
    (worktree / "notes.md").write_text("dirty tracked\n", encoding="utf-8")
    _git(["add", "notes.md"], worktree)
    (worktree / "extra.txt").write_text("dirty untracked\n", encoding="utf-8")
    (worktree / "secret.txt").write_text("denied\n", encoding="utf-8")
    reverted = revert_out_of_scope(worktree, ["README.md", "secret.txt"], ["secret.txt"])
    assert reverted == ["extra.txt", "notes.md", "secret.txt"]
    assert (worktree / "README.md").read_text(encoding="utf-8") == "kept\n"
    assert (worktree / "notes.md").read_text(encoding="utf-8") == "notes\n"
    assert not (worktree / "extra.txt").exists()
    assert not (worktree / "secret.txt").exists()


def test_integrate_disjoint_hunks_and_reports_a_conflict(git_repo: tuple[Path, str, list[Path]]) -> None:
    repo, run_id, made = git_repo
    alpha = _add(repo, run_id, "alpha", made)
    beta = _add(repo, run_id, "beta", made)
    _commit(alpha, "README.md", "ALPHA\nmiddle\nbeta\n")
    _commit(beta, "README.md", "alpha\nmiddle\nBETA\n")
    main_head = _git(["rev-parse", "HEAD"], repo).strip()
    clean = integrate(
        repo,
        run_id,
        ["alpha", "beta", "later"],
        {"alpha": f"tf/{run_id}/alpha", "beta": f"tf/{run_id}/beta"},
        "main",
        worktree_root=_short_root(),
    )
    assert clean.merged == ["alpha", "beta"]
    assert clean.skipped == ["later"]
    assert clean.conflicts == []
    integrated_branch = clean.branch or ""
    assert integrated_branch == f"tf/{run_id}/integrated"
    assert clean.head_sha == _git(["rev-parse", integrated_branch], repo).strip()
    assert _git(["rev-parse", "HEAD"], repo).strip() == main_head
    merged = _git(["show", f"{integrated_branch}:README.md"], repo)
    assert "ALPHA" in merged and "BETA" in merged
    assert _git(["status", "--porcelain"], repo).strip() == ""

    remove_worktree(repo, _short_root() / run_id / "integrated")
    _git(["checkout", "-B", "main", "main"], repo)
    _git(["branch", "-D", integrated_branch], repo)
    _commit(beta, "README.md", "CHANGED\nmiddle\nbeta\n")
    alpha_sha = _git(["rev-parse", f"tf/{run_id}/alpha"], repo).strip()
    beta_sha = _git(["rev-parse", f"tf/{run_id}/beta"], repo).strip()
    conflict = integrate(
        repo,
        run_id,
        ["alpha", "beta"],
        {"alpha": f"tf/{run_id}/alpha", "beta": f"tf/{run_id}/beta"},
        "main",
        worktree_root=_short_root(),
    )
    made.append(_short_root() / run_id / "integrated")
    assert conflict.merged == ["alpha"]
    assert len(conflict.conflicts) == 1
    assert conflict.conflicts[0].path == "README.md"
    assert set(conflict.conflicts[0].between) == {"alpha", "beta"}
    assert _git(["rev-parse", f"tf/{run_id}/alpha"], repo).strip() == alpha_sha
    assert _git(["rev-parse", f"tf/{run_id}/beta"], repo).strip() == beta_sha
    assert _git(["status", "--porcelain"], repo).strip() == ""
    merge_head = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        shell=False,
        check=False,
    )
    assert merge_head.returncode != 0
    assert "<<<<<<<" not in _git(["show", "HEAD:README.md"], repo)
    assert "CHANGED" not in _git(["show", f"tf/{run_id}/alpha:README.md"], repo)
    assert _git(["show", f"tf/{run_id}/beta:README.md"], repo).startswith("CHANGED\n")


def test_prepare_repo_rejects_a_dirty_tree_and_clones(git_repo: tuple[Path, str, list[Path]], tmp_path: Path) -> None:
    repo, _run_id, _made = git_repo
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(GitError, match="not clean"):
        prepare_repo(repo)
    cloned = prepare_repo(tmp_path / "clone", clone_from=repo)
    assert (cloned / "notes.md").is_file()
    assert _git(["status", "--porcelain"], cloned).strip() == ""


@pytest.mark.asyncio
async def test_gitops_tools_return_ok_or_a_typed_error(git_repo: tuple[Path, str, list[Path]]) -> None:
    repo, run_id, made = git_repo
    worktree = _add(repo, run_id, "alpha", made)
    _commit(worktree, "README.md", "ALPHA\nmiddle\nbeta\n")
    (worktree / "sneak.txt").write_text("outside\n", encoding="utf-8")
    blob = "x" * 200_001
    (repo / "big.txt").write_text(blob, encoding="utf-8", newline="\n")
    _git(["add", "big.txt"], repo)
    _git(["commit", "-m", "big"], repo)
    server = build_gitops_server(
        repo,
        "main",
        {"alpha": (["README.md"], [])},
    )
    listed = await server.list_tools()
    assert {tool.name for tool in listed} == READ_ONLY_TOOLS

    async def call(name: str, arguments: dict[str, object] | None = None) -> ToolResult:
        result = await server.call_tool(name, arguments or {})
        assert isinstance(result, CallToolResult)
        payload = result.structured_content
        assert isinstance(payload, dict)
        return ToolResult.model_validate(payload)

    trees = await call("worktree_list")
    assert trees.ok and trees.data is not None
    assert any(item["branch"] == f"tf/{run_id}/alpha" for item in trees.data["entries"])

    summary = await call("branch_summary", {"branch": f"tf/{run_id}/alpha"})
    assert summary.ok and summary.data is not None
    assert summary.data["files"][0]["path"] == "README.md"
    assert summary.data["commits"]

    missing = await call("branch_summary", {"branch": "tf/missing"})
    assert missing.ok is False and missing.error is not None
    assert missing.error.code == "NOT_FOUND"

    diff = await call("diff", {"branch": f"tf/{run_id}/alpha", "max_chars": 20})
    assert diff.ok and diff.data is not None
    assert diff.data["truncated"] is True
    escaped = await call("diff", {"branch": f"tf/{run_id}/alpha", "path": "../secret"})
    assert escaped.ok is False and escaped.error is not None and escaped.error.code == "PATH_ESCAPE"

    shown = await call("show_file", {"ref": "main", "path": "notes.md"})
    assert shown.ok and shown.data is not None and "notes" in shown.data["text"]
    absent = await call("show_file", {"ref": "main", "path": "nope.txt"})
    assert absent.ok is False and absent.error is not None and absent.error.code == "NOT_FOUND"
    outside = await call("show_file", {"ref": "main", "path": "C:/Windows/notepad.exe"})
    assert outside.ok is False and outside.error is not None and outside.error.code == "PATH_ESCAPE"
    huge = await call("show_file", {"ref": "main", "path": "big.txt"})
    assert huge.ok is False and huge.error is not None and huge.error.code == "TOO_LARGE"

    scope = await call("scope_check", {"task_id": "alpha"})
    assert scope.ok and scope.data is not None and scope.data["outside"] == ["sneak.txt"]
    unknown = await call("scope_check", {"task_id": "missing"})
    assert unknown.ok is False and unknown.error is not None and unknown.error.code == "BAD_ARGS"
