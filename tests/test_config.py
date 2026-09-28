from pathlib import Path

import pytest

from esport_agent.config import Settings


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("LOLESPORTS_API_KEY", "lolesports-test")
    for var in (
        "CLAUDE_MODEL",
        "DEFAULT_TEAM",
        "SQLITE_PATH",
        "LEAGUEPEDIA_BOT_USERNAME",
        "LOLESPORTS_LEAGUES",
    ):
        monkeypatch.delenv(var, raising=False)

    settings = Settings(_env_file=None)

    assert settings.anthropic_api_key.get_secret_value() == "sk-test"
    assert settings.lolesports_api_key.get_secret_value() == "lolesports-test"
    assert settings.claude_model == "claude-haiku-4-5-20251001"
    assert settings.default_team == "Karmine Corp"
    assert settings.leaguepedia_bot_username is None
    assert settings.leaguepedia_bot_password is None
    assert settings.sqlite_path == Path("esport_agent.db")
    assert settings.lolesports_leagues == ["lec", "lfl", "worlds", "msi", "first_stand"]


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("LOLESPORTS_API_KEY", "lolesports-test")
    monkeypatch.setenv("DEFAULT_TEAM", "G2 Esports")
    monkeypatch.setenv("LEAGUEPEDIA_BOT_PASSWORD", "secret")
    monkeypatch.setenv("LOLESPORTS_LEAGUES", '["lec", "lck"]')

    settings = Settings(_env_file=None)

    assert settings.default_team == "G2 Esports"
    assert settings.lolesports_leagues == ["lec", "lck"]
    assert settings.leaguepedia_bot_password is not None
    assert "secret" not in repr(settings)


@pytest.mark.parametrize("missing", ["ANTHROPIC_API_KEY", "LOLESPORTS_API_KEY"])
def test_api_keys_are_required(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("LOLESPORTS_API_KEY", "lolesports-test")
    monkeypatch.delenv(missing)

    with pytest.raises(ValueError, match=missing.lower()):
        Settings(_env_file=None)
