"""Worker process. Binds to 127.0.0.1 and serves one A2A task at a time."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from treefleet.worker.a2a_server import bearer_app, build_app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="treefleet-worker")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args(argv)
    os.chdir(args.worktree)
    url = f"http://127.0.0.1:{args.port}"
    application = build_app(Path(args.worktree), args.task_id, url)
    uvicorn.run(bearer_app(application.build(), args.token), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
