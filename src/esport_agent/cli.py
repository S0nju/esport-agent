"""Interactive loop to test the agent locally.

Usage: `uv run python -m esport_agent.cli`
"""

import logging
from contextlib import closing

import anthropic

from esport_agent.agent import Agent, AgentError
from esport_agent.config import MissingSettingError, get_settings, require_secret
from esport_agent.db import connect, init_schema

EXIT_COMMANDS = frozenset({"quit", "exit", "q"})


def repl(agent: Agent) -> None:
    """Read questions from standard input and print the agent's answers.

    A failed question (API error, interruption with Ctrl+C) is reported and the loop goes
    on, so one error does not end the session.
    """
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
        try:
            print(agent.ask(question))
        except KeyboardInterrupt:
            print("Interrupted.")
        except (anthropic.APIError, AgentError) as exc:
            print(f"Error: {exc}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    settings = get_settings()
    try:
        api_key = require_secret(settings.anthropic_api_key, "ANTHROPIC_API_KEY")
    except MissingSettingError as exc:
        raise SystemExit(str(exc)) from exc
    if not settings.sqlite_path.exists():
        raise SystemExit(
            f"No database at {settings.sqlite_path}. "
            "Run `uv run python -m esport_agent.sync` first."
        )
    client = anthropic.Anthropic(api_key=api_key)
    with closing(connect(settings.sqlite_path)) as conn:
        init_schema(conn)
        repl(Agent(client, conn, settings))


if __name__ == "__main__":
    main()
