import sqlite3
from pathlib import Path

import pytest
from discord import Locale, app_commands
from discord.app_commands import locale_str

from esport_agent.bot import app
from esport_agent.bot.app import EsportBot, is_allowed
from esport_agent.bot.translations import COMMAND_TEXTS, TEXTS, CommandTranslator, language
from esport_agent.config import Settings

GUILD_ID = 1555331755454767124


@pytest.fixture
def bot_settings() -> Settings:
    return Settings(_env_file=None, discord_bot_token="token", discord_guild_ids=[GUILD_ID])


def test_only_allowed_servers_and_never_direct_messages(bot_settings: Settings) -> None:
    assert is_allowed(GUILD_ID, bot_settings)
    assert not is_allowed(42, bot_settings)
    assert not is_allowed(None, bot_settings)


@pytest.mark.parametrize(
    ("locale", "expected"),
    [(Locale.french, "fr"), (Locale.american_english, "en"), ("de", "en"), ("fr", "fr")],
)
def test_language_follows_discord_and_falls_back_to_english(
    locale: Locale | str, expected: str
) -> None:
    assert language(locale) == expected


def test_every_text_exists_in_every_language() -> None:
    assert all(set(texts) == {"fr", "en"} for texts in TEXTS.values())


def test_commands_and_their_options(bot_settings: Settings, conn: sqlite3.Connection) -> None:
    bot = EsportBot(bot_settings, conn)

    commands = {c.name: c for c in bot.tree.get_commands()}

    assert set(commands) == {"roster", "next", "results"}
    roster = commands["roster"]
    assert isinstance(roster, app_commands.Command)
    assert [p.name for p in roster.parameters] == ["team", "league", "staff"]
    assert not any(p.required for p in roster.parameters)
    results = commands["results"]
    assert isinstance(results, app_commands.Command)
    limit = results.get_parameter("limit")
    assert limit is not None
    assert (limit.min_value, limit.max_value, limit.default) == (1, 10, 5)


async def test_command_descriptions_are_translated(
    bot_settings: Settings, conn: sqlite3.Connection
) -> None:
    translator = CommandTranslator()
    bot = EsportBot(bot_settings, conn)
    commands = [c for c in bot.tree.get_commands() if isinstance(c, app_commands.Command)]
    descriptions = [c.description for c in commands]
    descriptions += [p.description for c in commands for p in c.parameters]

    for description in descriptions:
        assert description in COMMAND_TEXTS
    french = await translator.translate(
        locale_str("Current roster of a team"),
        Locale.french,
        None,  # type: ignore[arg-type]
    )
    english = await translator.translate(
        locale_str("Current roster of a team"),
        Locale.british_english,
        None,  # type: ignore[arg-type]
    )
    assert (french, english) == ("Roster actuel d'une équipe", None)


def test_main_requires_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "get_settings", lambda: Settings(_env_file=None))

    with pytest.raises(SystemExit, match="DISCORD_BOT_TOKEN"):
        app.main()


def test_main_requires_allowed_servers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = Settings(_env_file=None, discord_bot_token="token", sqlite_path=tmp_path / "db")
    monkeypatch.setattr(app, "get_settings", lambda: settings)

    with pytest.raises(SystemExit, match="DISCORD_GUILD_IDS"):
        app.main()
