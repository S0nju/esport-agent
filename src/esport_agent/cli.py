"""Interactive loop to test the agent locally.

Usage:
- `uv run python -m esport_agent.cli`: ask the agent questions (needs an Anthropic key).
- `uv run python -m esport_agent.cli --tools`: call the tools directly, without Claude and
  for free, e.g. `get_team_recent_results team="Karmine Corp" limit=3`.
"""

import argparse
import json
import logging
import shlex
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, datetime

import anthropic

from esport_agent.agent import Agent, AgentError
from esport_agent.config import MissingSettingError, Settings, get_settings, require_secret
from esport_agent.db import connect, init_schema
from esport_agent.tools.definitions import TOOLS
from esport_agent.tools.handlers import ToolContext, UnknownToolError, execute_tool

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
            answer = agent.ask(question)
        except KeyboardInterrupt:
            print("Interrupted.")
        except AgentError as exc:
            print(f"Error: {exc}")
            if exc.usage is not None:
                print(f"[{exc.usage.summary()}]")
        except anthropic.APIError as exc:
            print(f"Error: {exc}")
        else:
            print(answer.text)
            print(f"[{answer.usage.summary()}]")


def parse_tool_call(line: str) -> tuple[str, dict[str, object]]:
    """Parse `tool_name key=value ...`; digit-only values become integers.

    Values with spaces must be quoted: `team="Karmine Corp"`.
    """
    name, *args = shlex.split(line)
    tool_input: dict[str, object] = {}
    for arg in args:
        key, sep, value = arg.partition("=")
        if not sep or not key:
            raise ValueError(f"Expected key=value, got {arg!r}")
        tool_input[key] = int(value) if value.isdigit() else value
    return name, tool_input


def tools_repl(conn: sqlite3.Connection, settings: Settings) -> None:
    """Read tool calls from standard input and print their JSON result, without Claude."""
    print("Call a tool: <tool> [key=value ...] (help for the list, quit to exit).")
    while True:
        try:
            line = input("tool> ").strip()
        except EOFError, KeyboardInterrupt:
            print()
            return
        if not line:
            continue
        if line.lower() in EXIT_COMMANDS:
            return
        if line.lower() == "help":
            for tool in TOOLS:
                schema = tool["input_schema"]
                properties = schema.get("properties") if isinstance(schema, dict) else None
                params = ", ".join(properties) if isinstance(properties, dict) else ""
                print(f"- {tool['name']} ({params}): {tool.get('description', '')}")
            continue
        try:
            name, tool_input = parse_tool_call(line)
            ctx = ToolContext.from_settings(settings, datetime.now(UTC))
            output = execute_tool(conn, name, tool_input, ctx)
        except (ValueError, UnknownToolError) as exc:
            print(f"Error: {exc}")
            continue
        print(json.dumps(json.loads(output), ensure_ascii=False, indent=2))


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Test the esports agent locally.")
    parser.add_argument(
        "--tools",
        action="store_true",
        help="call the tools directly, without Claude (no Anthropic key needed)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)
    settings = get_settings()
    api_key = ""
    if not args.tools:
        try:
            api_key = require_secret(settings.anthropic_api_key, "ANTHROPIC_API_KEY")
        except MissingSettingError as exc:
            raise SystemExit(str(exc)) from exc
    if not settings.sqlite_path.exists():
        raise SystemExit(
            f"No database at {settings.sqlite_path}. "
            "Run `uv run python -m esport_agent.sync` first."
        )
    with closing(connect(settings.sqlite_path)) as conn:
        init_schema(conn)
        if args.tools:
            tools_repl(conn, settings)
        else:
            repl(Agent(anthropic.Anthropic(api_key=api_key), conn, settings))


if __name__ == "__main__":
    main()
