from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import anthropic
import pytest

from esport_agent import cli
from esport_agent.agent import AgentError
from esport_agent.cli import repl
from esport_agent.config import Settings


def feed_input(monkeypatch: pytest.MonkeyPatch, *lines: str) -> None:
    inputs: Iterator[str] = iter(lines)
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))


def test_repl_asks_agent_until_quit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    feed_input(monkeypatch, "", "Next match?", "quit")
    agent = MagicMock()
    agent.ask.return_value = "Saturday at 6 pm"

    repl(agent)

    agent.ask.assert_called_once_with("Next match?")
    assert "Saturday at 6 pm" in capsys.readouterr().out


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
    agent.ask.side_effect = [error, "Second answer"]

    repl(agent)

    assert agent.ask.call_count == 2
    output = capsys.readouterr().out
    assert "Second answer" in output
    assert "Error:" in output or "Interrupted." in output


def test_main_requires_the_anthropic_key(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, anthropic_api_key=None)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    with pytest.raises(SystemExit, match="ANTHROPIC_API_KEY"):
        cli.main()


def test_main_requires_a_synced_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None, anthropic_api_key="sk-test", sqlite_path=tmp_path / "missing.db"
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    with pytest.raises(SystemExit, match=r"esport_agent\.sync"):
        cli.main()
    assert not (tmp_path / "missing.db").exists()
