from unittest.mock import MagicMock

import pytest

from esport_agent.cli import repl


def test_repl_asks_agent_until_quit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = iter(["", "Next match?", "quit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(inputs))
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
