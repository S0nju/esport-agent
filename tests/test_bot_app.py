import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from anthropic.types import Message
from discord import Locale, app_commands
from discord.app_commands import locale_str

from esport_agent.agent import Answer
from esport_agent.bot import app
from esport_agent.bot.app import EsportBot, is_allowed
from esport_agent.bot.ask import ASK_MAX_TOKENS, MAX_QUESTION_LENGTH, pseudonymize
from esport_agent.bot.translations import COMMAND_TEXTS, TEXTS, CommandTranslator, language
from esport_agent.config import Settings
from esport_agent.db import connect, init_schema
from esport_agent.usage import Usage

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


def fake_ask(question: str) -> Answer:
    return Answer(text=f"Answer to {question}", usage=Usage())


def test_commands_and_their_options(bot_settings: Settings, conn: sqlite3.Connection) -> None:
    bot = EsportBot(bot_settings, conn, "key", fake_ask)

    commands = {c.name: c for c in bot.tree.get_commands()}

    assert set(commands) == {"roster", "next", "results", "ask"}
    ask = commands["ask"]
    assert isinstance(ask, app_commands.Command)
    question = ask.get_parameter("question")
    assert question is not None
    assert (question.required, question.max_value) == (True, MAX_QUESTION_LENGTH)
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
    bot = EsportBot(bot_settings, conn, "key", fake_ask)
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


def test_ask_is_only_offered_with_an_anthropic_key(
    bot_settings: Settings, conn: sqlite3.Connection
) -> None:
    bot = EsportBot(bot_settings, conn, "key", ask=None)

    assert {c.name for c in bot.tree.get_commands()} == {"roster", "next", "results"}


def test_make_ask_answers_on_its_own_connection(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, sqlite_path=tmp_path / "db")
    client = MagicMock()
    client.messages.create.return_value = Message.model_validate(
        {
            "id": "msg",
            "type": "message",
            "role": "assistant",
            "model": "claude-haiku-4-5",
            "content": [{"type": "text", "text": "Hello"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    )
    with closing(connect(settings.sqlite_path)) as conn:
        init_schema(conn)

    ask = app.make_ask(settings, client)

    assert ask("Hi").text == "Hello"
    assert client.messages.create.call_args.kwargs["max_tokens"] == ASK_MAX_TOKENS


def test_main_requires_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "get_settings", lambda: Settings(_env_file=None))

    with pytest.raises(SystemExit, match="DISCORD_BOT_TOKEN"):
        app.main()


def test_main_requires_a_user_hash_key(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, discord_bot_token="token")
    monkeypatch.setattr(app, "get_settings", lambda: settings)

    with pytest.raises(SystemExit, match="DISCORD_USER_HASH_KEY"):
        app.main()


def test_main_requires_allowed_servers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        discord_bot_token="token",
        discord_user_hash_key="key",
        sqlite_path=tmp_path / "db",
    )
    monkeypatch.setattr(app, "get_settings", lambda: settings)

    with pytest.raises(SystemExit, match="DISCORD_GUILD_IDS"):
        app.main()


async def test_free_commands_are_recorded_with_a_pseudonym(
    bot_settings: Settings, conn: sqlite3.Connection
) -> None:
    bot = EsportBot(bot_settings, conn, "key")
    sent: list[str] = []

    async def send_message(message: str, **kwargs: object) -> None:
        sent.append(message)

    interaction = SimpleNamespace(
        client=bot,
        guild_id=GUILD_ID,
        user=SimpleNamespace(id=42),
        locale=Locale.french,
        response=SimpleNamespace(send_message=send_message),
    )

    await app._reply(
        interaction,  # type: ignore[arg-type]
        "roster",
        lambda bot, lang: f"reply in {lang}",
        team="KC",
    )

    assert sent == ["reply in fr"]
    row = conn.execute("SELECT * FROM requests").fetchone()
    assert (row["command"], row["question"], row["answer"]) == (
        "roster",
        '{"team": "KC"}',
        "reply in fr",
    )
    assert row["user_hash"] == pseudonymize(42, "key")
    assert row["cost_usd"] == 0
