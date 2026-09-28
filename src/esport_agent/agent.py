"""Agent loop: call Claude, run the requested tools, return the final answer."""

import logging
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from importlib.resources import files

import anthropic
from anthropic.types import MessageParam, TextBlockParam, ToolResultBlockParam

from esport_agent.config import Settings
from esport_agent.tools.definitions import TOOLS
from esport_agent.tools.handlers import execute_tool

logger = logging.getLogger(__name__)

MAX_TOKENS = 4096
MAX_TOOL_ROUNDS = 5


class AgentError(RuntimeError):
    """The agent could not produce an answer."""


def load_system_prompt() -> str:
    """Load the versioned system prompt from `prompts/system.md`."""
    return files("esport_agent.prompts").joinpath("system.md").read_text(encoding="utf-8")


class Agent:
    """Answer a natural-language question using the tools."""

    def __init__(
        self,
        client: anthropic.Anthropic,
        conn: sqlite3.Connection,
        settings: Settings,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._conn = conn
        self._settings = settings
        self._clock = clock
        self._system_prompt = load_system_prompt()

    def ask(self, question: str) -> str:
        """Ask the agent a question and return its text answer."""
        tz = self._settings.tzinfo
        now = self._clock().astimezone(tz)
        # The static prompt comes first and today's date last, so the prompt prefix stays
        # identical across questions.
        system: list[TextBlockParam] = [
            {"type": "text", "text": self._system_prompt},
            {
                "type": "text",
                "text": f"Current date and time: {now:%A %Y-%m-%d %H:%M} ({tz.key}).",
            },
        ]
        messages: list[MessageParam] = [{"role": "user", "content": question}]

        for _ in range(MAX_TOOL_ROUNDS + 1):
            response = self._client.messages.create(
                model=self._settings.claude_model,
                max_tokens=MAX_TOKENS,
                system=system,
                tools=TOOLS,
                messages=messages,
            )
            if response.stop_reason != "tool_use":
                answer = "".join(b.text for b in response.content if b.type == "text").strip()
                if not answer:
                    raise AgentError(f"Empty answer (stop_reason={response.stop_reason})")
                if response.stop_reason != "end_turn":
                    logger.warning("Answer cut short: stop_reason=%s", response.stop_reason)
                return answer

            messages.append({"role": "assistant", "content": response.content})
            results: list[ToolResultBlockParam] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                logger.info("Tool %s called with %s", block.name, block.input)
                try:
                    content = execute_tool(
                        self._conn,
                        block.name,
                        block.input,
                        default_team=self._settings.default_team,
                        now=now,
                        tz=tz,
                    )
                    results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": content}
                    )
                except Exception as exc:
                    # Any tool error is sent back to Claude instead of breaking the loop.
                    logger.exception("Tool %s failed", block.name)
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"Error: {exc}",
                            "is_error": True,
                        }
                    )
            messages.append({"role": "user", "content": results})

        raise AgentError(f"No final answer after {MAX_TOOL_ROUNDS} tool rounds")
