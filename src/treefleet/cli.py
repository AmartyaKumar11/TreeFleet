"""Folder-first CLI. `run` validates and prints a RunRequest. The run itself is Phase 4."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from treefleet.config import load_settings
from treefleet.contracts import RunRequest
from treefleet.cursor_models import UnknownModelError, discover, require_known


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "models":
        return _models()
    return _run(args)


def _models() -> int:
    settings = load_settings(Path.cwd() / "treefleet.toml")
    for model in discover(Path(settings.cursor.cli), settings.cursor.list_flag):
        print(model)
    return 0


def _run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="treefleet")
    parser.add_argument("goal")
    parser.add_argument("--model", default="auto")
    parser.add_argument("--repo", default=None)
    parser.add_argument("--no-approval", action="store_true")
    parsed = parser.parse_args(argv)
    worker_model = "router" if parsed.model == "auto" else parsed.model
    settings = load_settings(Path.cwd() / "treefleet.toml")
    try:
        require_known(worker_model, Path(settings.cursor.cli), settings.cursor.list_flag)
    except UnknownModelError as exc:
        raise SystemExit(str(exc)) from None
    request = RunRequest(goal=parsed.goal, repo=parsed.repo or str(Path.cwd()), worker_model=worker_model)
    print(request.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
