import json
import sqlite3
from unittest.mock import patch

import pytest

from esport_agent.tools import handlers


@pytest.mark.parametrize(
    "func",
    [handlers.get_team_roster, handlers.get_team_next_match, handlers.get_team_recent_results],
)
def test_tools_not_implemented(conn: sqlite3.Connection, func: object) -> None:
    assert callable(func)
    with pytest.raises(NotImplementedError):
        func(conn, "Some Team")


def test_execute_tool_uses_given_team(conn: sqlite3.Connection) -> None:
    roster = [{"player": "Alpha", "role": "Top"}]
    with patch.object(handlers, "get_team_roster", return_value=roster) as tool:
        result = handlers.execute_tool(conn, "get_team_roster", {"team": "Team A"}, "Default")

    tool.assert_called_once_with(conn, "Team A")
    assert json.loads(result) == roster


@pytest.mark.parametrize("tool_input", [{}, {"team": ""}, {"team": "  "}, {"team": 42}])
def test_execute_tool_falls_back_to_default_team(
    conn: sqlite3.Connection, tool_input: dict[str, object]
) -> None:
    with patch.object(handlers, "get_team_next_match", return_value=None) as tool:
        result = handlers.execute_tool(conn, "get_team_next_match", tool_input, "Default")

    tool.assert_called_once_with(conn, "Default")
    assert json.loads(result) is None


@pytest.mark.parametrize(
    ("tool_input", "expected_limit"),
    [({}, handlers.DEFAULT_RESULTS_LIMIT), ({"limit": 3}, 3), ({"limit": "3"}, 5)],
)
def test_execute_tool_recent_results_limit(
    conn: sqlite3.Connection, tool_input: dict[str, object], expected_limit: int
) -> None:
    with patch.object(handlers, "get_team_recent_results", return_value=[]) as tool:
        handlers.execute_tool(conn, "get_team_recent_results", tool_input, "Default")

    tool.assert_called_once_with(conn, "Default", expected_limit)


def test_execute_tool_unknown(conn: sqlite3.Connection) -> None:
    with pytest.raises(handlers.UnknownToolError):
        handlers.execute_tool(conn, "does_not_exist", {}, "Default")
