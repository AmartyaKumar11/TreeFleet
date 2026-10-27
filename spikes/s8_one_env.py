"""S8: import ADK and CrewAI from the same interpreter."""

from __future__ import annotations

from importlib.metadata import version

import crewai
import google.adk

def main() -> None:
    print(f"imported {google.adk.__name__} {crewai.__name__}")
    print(f"google-adk {version('google-adk')}")
    print(f"crewai {version('crewai')}")
    print("Result: PASS")


if __name__ == "__main__":
    main()
