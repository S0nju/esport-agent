from pathlib import Path

import pytest
from pydantic import SecretStr

from esport_agent.config import MissingSettingError, Settings, require_secret


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("LOLESPORTS_API_KEY", "lolesports-test")
    for var in (
        "CLAUDE_MODEL",
        "DEFAULT_TEAM",
        "SQLITE_PATH",
        "LEAGUEPEDIA_BOT_USERNAME",
        "LOLESPORTS_LEAGUES",
        "TIMEZONE",
        "PREFERRED_LEAGUES",
        "LOG_LEVEL",
        "LOG_FILE",
    ):
        monkeypatch.delenv(var, raising=False)

    settings = Settings(_env_file=None)

    assert require_secret(settings.anthropic_api_key, "ANTHROPIC_API_KEY") == "sk-test"
    assert require_secret(settings.lolesports_api_key, "LOLESPORTS_API_KEY") == "lolesports-test"
    assert settings.claude_model == "claude-haiku-4-5-20251001"
    assert settings.default_team == "Karmine Corp"
    assert settings.leaguepedia_bot_username is None
    assert settings.leaguepedia_bot_password is None
    assert settings.sqlite_path == Path("esport_agent.db")
    assert settings.lolesports_leagues == ["lec", "lfl", "worlds", "msi", "first_stand"]
    assert settings.tzinfo.key == "Europe/Paris"
    assert settings.preferred_leagues == ["lec"]
    assert settings.log_level is None
    assert settings.log_file is None


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


@pytest.mark.parametrize("var", ["ANTHROPIC_API_KEY", "LOLESPORTS_API_KEY"])
def test_api_keys_are_optional(monkeypatch: pytest.MonkeyPatch, var: str) -> None:
    monkeypatch.delenv(var, raising=False)

    settings = Settings(_env_file=None)

    assert getattr(settings, var.lower()) is None


def test_require_secret_returns_the_value() -> None:
    assert require_secret(SecretStr("sk-test"), "ANTHROPIC_API_KEY") == "sk-test"


@pytest.mark.parametrize("value", [None, SecretStr("")])
def test_require_secret_names_the_missing_variable(value: SecretStr | None) -> None:
    with pytest.raises(MissingSettingError, match="ANTHROPIC_API_KEY"):
        require_secret(value, "ANTHROPIC_API_KEY")


def test_timezone_override() -> None:
    assert Settings(_env_file=None, timezone="America/New_York").tzinfo.key == "America/New_York"


def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(ValueError, match="Mars/Olympus"):
        Settings(_env_file=None, timezone="Mars/Olympus")


@pytest.mark.parametrize(("value", "expected"), [("debug", "DEBUG"), (" Info ", "INFO")])
def test_log_level_is_normalized(value: str, expected: str) -> None:
    assert Settings(_env_file=None, log_level=value).log_level == expected


def test_unknown_log_level_is_rejected() -> None:
    with pytest.raises(ValueError, match="VERBOSE"):
        Settings(_env_file=None, log_level="VERBOSE")
