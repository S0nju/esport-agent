import sqlite3
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import anthropic
import pytest

from esport_agent import cli
from esport_agent.agent import AgentError, Answer
from esport_agent.cli import parse_tool_call, repl, tools_repl
from esport_agent.config import Settings
from esport_agent.db import connect, init_schema, replace_teams
from esport_agent.usage import Usage
from tests.factories import make_player, make_team


def feed_input(monkeypatch: pytest.MonkeyPatch, *lines: str) -> None:
    inputs: Iterator[str] = iter(lines)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))


def test_repl_asks_agent_until_quit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    feed_input(monkeypatch, "", "Next match?", "quit")
    agent = MagicMock()
    usage = Usage(model="claude-haiku-4-5", calls=1, input_tokens=1000, output_tokens=100)
    agent.ask.return_value = Answer(text="Saturday at 6 pm", usage=usage)

    repl(agent)

    agent.ask.assert_called_once_with("Next match?")
    output = capsys.readouterr().out
    assert "Saturday at 6 pm" in output
    assert "[claude-haiku-4-5 · 1 call · 1,000 in / 100 out tokens · ≈ $0.0015]" in output


def test_repl_stops_on_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    def raise_eof(_prompt: str) -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)
    agent = MagicMock()

    repl(agent)

    agent.ask.assert_not_called()


@pytest.mark.parametrize(
    "error",
    [
        AgentError("no answer"),
        anthropic.APIConnectionError(request=MagicMock()),
        KeyboardInterrupt(),
    ],
)
def test_repl_keeps_running_after_a_failed_question(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: BaseException
) -> None:
    feed_input(monkeypatch, "First?", "Second?", "quit")
    agent = MagicMock()
    agent.ask.side_effect = [error, Answer(text="Second answer", usage=Usage())]

    repl(agent)

    assert agent.ask.call_count == 2
    output = capsys.readouterr().out
    assert "Second answer" in output
    assert "Error:" in output or "Interrupted." in output


def test_main_requires_the_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, anthropic_api_key=None)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        cli.main([])


def test_main_requires_a_synced_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None, anthropic_api_key="sk-test", sqlite_path=tmp_path / "missing.db"
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    with pytest.raises(SystemExit, match=r"esport_agent\.sync"):
        cli.main([])
    assert not (tmp_path / "missing.db").exists()


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("get_team_roster", ("get_team_roster", {})),
        ("get_team_roster team=KC", ("get_team_roster", {"team": "KC"})),
        (
            'get_team_recent_results team="Karmine Corp" limit=3',
            ("get_team_recent_results", {"team": "Karmine Corp", "limit": 3}),
        ),
    ],
)
def test_parse_tool_call(line: str, expected: tuple[str, dict[str, object]]) -> None:
    assert parse_tool_call(line) == expected


@pytest.mark.parametrize("line", ["get_team_roster KC", "get_team_roster =KC", 'x team="open'])
def test_parse_tool_call_rejects_malformed_input(line: str) -> None:
    with pytest.raises(ValueError):
        parse_tool_call(line)


def test_tools_repl_calls_tools_without_claude(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    conn: sqlite3.Connection,
    settings: Settings,
) -> None:
    replace_teams(conn, [make_team("kc", (make_player("Caliste", "bottom"),), name="Karmine Corp")])
    feed_input(monkeypatch, "help", "get_team_roster", "nope", "get_team_roster KC", "quit")

    tools_repl(conn, settings)

    output = capsys.readouterr().out
    assert "- get_team_roster (team):" in output
    assert '"summoner_name": "Caliste"' in output
    assert "Error: Unknown tool: nope" in output
    assert "Error: Expected key=value" in output


def test_main_tools_mode_needs_no_anthropic_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "esport.db"
    with connect(db_path) as conn:
        init_schema(conn)
    conn.close()
    settings = Settings(_env_file=None, anthropic_api_key=None, sqlite_path=db_path)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    calls: list[Settings] = []
    monkeypatch.setattr(cli, "tools_repl", lambda _conn, s: calls.append(s))

    cli.main(["--tools"])

    assert calls == [settings]


def test_repl_shows_the_usage_of_a_failed_question(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    feed_input(monkeypatch, "Question?", "quit")
    agent = MagicMock()
    usage = Usage(model="claude-haiku-4-5", calls=6, input_tokens=9000, output_tokens=300)
    agent.ask.side_effect = AgentError("No final answer", usage)

    repl(agent)

    output = capsys.readouterr().out
    assert "Error: No final answer" in output
    assert "[claude-haiku-4-5 · 6 calls" in output
