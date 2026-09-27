from pathlib import Path

import pytest

from esport_agent.config import Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    for var in ("CLAUDE_MODEL", "DEFAULT_TEAM", "SQLITE_PATH", "LEAGUEPEDIA_BOT_USERNAME"):
        monkeypatch.delenv(var, raising=False)

    settings = Settings(_env_file=None)

    assert settings.anthropic_api_key.get_secret_value() == "sk-test"
    assert settings.claude_model == "claude-haiku-4-5-20251001"
    assert settings.default_team == "Karmine Corp"
    assert settings.leaguepedia_bot_username is None
    assert settings.leaguepedia_bot_password is None
    assert settings.sqlite_path == Path("esport_agent.db")


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("DEFAULT_TEAM", "G2 Esports")
    monkeypatch.setenv("LEAGUEPEDIA_BOT_PASSWORD", "secret")

    settings = Settings(_env_file=None)

    assert settings.default_team == "G2 Esports"
    assert settings.leaguepedia_bot_password is not None
    assert "secret" not in repr(settings)


def test_api_key_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(ValueError, match="anthropic_api_key"):
        Settings(_env_file=None)
