"""S2: CrewAI on DeepSeek calls an MCP tool. Never prints secrets."""

from __future__ import annotations

import os
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path

SERVER = """
import os
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("s2")

@mcp.tool()
def add(a: int, b: int) -> int:
    \"\"\"Add two integers.\"\"\"
    Path(os.environ["S2_MARKER"]).write_text(f"{int(a)}+{int(b)}\\n", encoding="utf-8")
    return int(a) + int(b)

if __name__ == "__main__":
    mcp.run()
"""


def load_env() -> None:
    for line in Path(".env").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def redact(text: str) -> str:
    for key in ("DEEPSEEK_API_KEY", "TYPESAFE_API_KEY"):
        secret = os.environ.get(key)
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def main() -> None:
    load_env()
    from crewai import Agent, Crew, Task
    from crewai.mcp.config import MCPServerStdio

    print(f"crewai {version('crewai')}")
    print(f"mcp {version('mcp')}")
    folder = Path(tempfile.mkdtemp(prefix="tfs2-"))
    server = folder / "server.py"
    marker = folder / "called.txt"
    server.write_text(SERVER, encoding="utf-8")
    agent = Agent(
        role="adder",
        goal="Call the add tool and report its result.",
        backstory="You only answer by calling tools.",
        llm="deepseek/deepseek-flash",
        mcps=[MCPServerStdio(command=sys.executable, args=[str(server)], env={"S2_MARKER": str(marker)})],
        verbose=False,
        max_iter=3,
    )
    task = Task(description="Use the add tool to add 2 and 3.", expected_output="5", agent=agent)
    try:
        Crew(agents=[agent], tasks=[task], verbose=False).kickoff()
    except Exception as exc:
        print(redact(f"error={type(exc).__name__}: {exc}"))
    called = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
    print(f"mcp_called={called == '2+3'} mechanism=MCPServerStdio")
    if called == "2+3":
        print("Result: PASS")
    else:
        print("Result: FALLBACK use the version's tool adapter for MCP")
    os._exit(0)


if __name__ == "__main__":
    main()
