import sqlite3
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from anthropic.types import Message

from esport_agent import agent as agent_module
from esport_agent.agent import MAX_TOOL_ROUNDS, Agent, AgentError, load_system_prompt
from esport_agent.config import Settings


# Any: hand-built content blocks, validated afterwards by Message.model_validate.
def make_message(content: list[dict[str, Any]], stop_reason: str) -> Message:
    return Message.model_validate(
        {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-test",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )


def text(value: str) -> dict[str, Any]:
    return {"type": "text", "text": value}


def tool_use(name: str, tool_input: dict[str, Any], tool_id: str = "toolu_1") -> dict[str, Any]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


def make_agent(conn: sqlite3.Connection, settings: Settings, *responses: Message) -> Agent:
    client = MagicMock()
    client.messages.create.side_effect = list(responses)
    return Agent(client, conn, settings)


def test_system_prompt_is_loaded() -> None:
    assert "League of Legends" in load_system_prompt()


def test_answer_without_tool(conn: sqlite3.Connection, settings: Settings) -> None:
    agent = make_agent(conn, settings, make_message([text("Hello")], "end_turn"))

    assert agent.ask("Hi") == "Hello"


def test_tool_call_then_answer(conn: sqlite3.Connection, settings: Settings) -> None:
    agent = make_agent(
        conn,
        settings,
        make_message([tool_use("get_team_roster", {})], "tool_use"),
        make_message([text("Here is the roster")], "end_turn"),
    )

    with patch.object(agent_module, "execute_tool", return_value="[]") as execute:
        answer = agent.ask("What is the roster?")

    assert answer == "Here is the roster"
    execute.assert_called_once_with(conn, "get_team_roster", {}, settings.default_team)
    second_call = agent._client.messages.create.call_args_list[1]  # type: ignore[attr-defined]
    tool_result = second_call.kwargs["messages"][-1]["content"][0]
    assert tool_result == {"type": "tool_result", "tool_use_id": "toolu_1", "content": "[]"}


def test_tool_error_is_sent_back_to_claude(conn: sqlite3.Connection, settings: Settings) -> None:
    agent = make_agent(
        conn,
        settings,
        make_message([tool_use("get_team_roster", {})], "tool_use"),
        make_message([text("Sorry")], "end_turn"),
    )

    with patch.object(agent_module, "execute_tool", side_effect=RuntimeError("boom")):
        assert agent.ask("Roster?") == "Sorry"

    second_call = agent._client.messages.create.call_args_list[1]  # type: ignore[attr-defined]
    tool_result = second_call.kwargs["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert "boom" in tool_result["content"]


def test_too_many_tool_rounds(conn: sqlite3.Connection, settings: Settings) -> None:
    responses = [
        make_message([tool_use("get_team_roster", {})], "tool_use")
        for _ in range(MAX_TOOL_ROUNDS + 1)
    ]
    agent = make_agent(conn, settings, *responses)

    with (
        patch.object(agent_module, "execute_tool", return_value="[]"),
        pytest.raises(AgentError),
    ):
        agent.ask("Roster?")
