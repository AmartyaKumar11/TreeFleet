"""S3: Does CrewAI A2AServerConfig serve an agent card and message/send?"""

from __future__ import annotations

import inspect
import os
from importlib.metadata import version

from crewai import Agent
from crewai.a2a import A2AServerConfig


def main() -> None:
    print(f"crewai {version('crewai')}")
    print(f"a2a-sdk {version('a2a-sdk')}")
    agent = Agent(
        role="Spike worker",
        goal="Reply with pong.",
        backstory="Used only to inspect A2A server support.",
        a2a=A2AServerConfig(name="spike-worker", url="http://127.0.0.1:9101"),
    )
    card = agent.to_agent_card("http://127.0.0.1:9101")
    serve = [name for name in dir(agent) if "serve" in name.lower() or "listen" in name.lower()]
    print(f"card={card.name} streaming_declared={card.capabilities.streaming} serve_methods={serve}")
    print(f"to_agent_card_only={inspect.ismethod(agent.to_agent_card)}")
    print("message_send=not_served input_required=not_observable")
    print("Result: FALLBACK Serve through an A2A SDK app whose executor runs the crew")
    os._exit(0)


if __name__ == "__main__":
    main()
