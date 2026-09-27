"""Interactive loop to test the agent locally.

Usage : `uv run python -m esport_agent.cli`
"""

import logging
from contextlib import closing

import anthropic

from esport_agent.agent import Agent
from esport_agent.config import get_settings
from esport_agent.db import connect, init_schema

EXIT_COMMANDS = frozenset({"quit", "exit", "q"})


def repl(agent: Agent) -> None:
    """Read questions from standard input and print the agent's answers."""
    print("Ask your question (quit to exit).")
    while True:
        try:
            question = input("> ").strip()
        except EOFError, KeyboardInterrupt:
            print()
            return
        if not question:
            continue
        if question.lower() in EXIT_COMMANDS:
            return
        print(agent.ask(question))


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    settings = get_settings()
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())
    with closing(connect(settings.sqlite_path)) as conn:
        init_schema(conn)
        repl(Agent(client, conn, settings))


if __name__ == "__main__":
    main()
