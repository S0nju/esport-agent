"""Discord bot: free slash commands (/roster, /next, /results) and /ask, through Claude.

The bot keeps a connection open to Discord (the gateway), which pushes each command to it
as an "interaction", to be answered within 3 seconds. The free commands read SQLite in a
few milliseconds and reply at once; /ask takes seconds, so it first tells Discord it is
thinking ("defer"), then sends the answer (see `ask.py` for its quotas). Commands are
registered on the allowed servers only (`DISCORD_GUILD_IDS`), where they show up at once,
and the bot leaves any other server. Every command is recorded in the `requests` table,
with a pseudonym instead of the user's id.

Run with `uv run python -m esport_agent.bot`; the database is filled by the sync.
"""

import json
import logging
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import UTC, datetime

import anthropic
import discord
from discord import app_commands
from discord.app_commands import locale_str

from esport_agent.agent import Agent, Answer
from esport_agent.bot.ask import (
    ASK_MAX_TOKENS,
    MAX_QUESTION_LENGTH,
    AskFunction,
    AskService,
    pseudonymize,
)
from esport_agent.bot.replies import next_match_reply, results_reply, roster_reply
from esport_agent.bot.translations import CommandTranslator, Lang, language, text
from esport_agent.config import MissingSettingError, Settings, get_settings, require_secret
from esport_agent.db import RequestRecord, connect, init_schema, record_request
from esport_agent.logging_config import setup_logging

logger = logging.getLogger(__name__)

NO_MENTIONS = discord.AllowedMentions.none()
"""Replies quote what users typed: never let them ping @everyone or anyone else."""

TEAM_HELP = locale_str("Team name, short name or code (default team if empty)")
LEAGUE_HELP = locale_str('League, to pick the team playing there (e.g. "LFL")')


class EsportBot(discord.Client):
    def __init__(
        self,
        settings: Settings,
        conn: sqlite3.Connection,
        user_hash_key: str,
        ask: AskFunction | None = None,
    ) -> None:
        # The bot never joins voice channels: no need to warn that voice is not installed.
        discord.VoiceClient.warn_nacl = discord.VoiceClient.warn_dave = False
        # Slash commands need no privileged intent; `guilds` tells the bot where it is.
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents, allowed_mentions=NO_MENTIONS)
        self.settings = settings
        self.conn = conn
        self.user_hash_key = user_hash_key
        # Without an Anthropic key, /ask is not offered: the free commands still work.
        self.ask_service = AskService(conn, settings, user_hash_key, ask) if ask else None
        self.tree = EsportCommandTree(self)
        add_commands(self.tree, with_ask=self.ask_service is not None)

    async def setup_hook(self) -> None:
        await self.tree.set_translator(CommandTranslator())
        for guild_id in self.settings.discord_guild_ids:
            guild = discord.Object(id=guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        logger.info("Commands registered on %d servers", len(self.settings.discord_guild_ids))

    async def on_ready(self) -> None:
        logger.info("Connected as %s", self.user)
        for guild in self.guilds:
            await self._leave_if_not_allowed(guild)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._leave_if_not_allowed(guild)

    async def _leave_if_not_allowed(self, guild: discord.Guild) -> None:
        if not is_allowed(guild.id, self.settings):
            logger.warning("Leaving server %s, which is not in DISCORD_GUILD_IDS", guild.id)
            await guild.leave()


class EsportCommandTree(app_commands.CommandTree[EsportBot]):
    async def interaction_check(self, interaction: discord.Interaction[EsportBot], /) -> bool:
        if is_allowed(interaction.guild_id, interaction.client.settings):
            return True
        await interaction.response.send_message(
            text("not_allowed", language(interaction.locale)), ephemeral=True
        )
        return False

    async def on_error(
        self, interaction: discord.Interaction[EsportBot], error: app_commands.AppCommandError, /
    ) -> None:
        # A bug in a command: log it, and still tell the user instead of leaving them waiting.
        logger.error("Command %s failed", interaction.command, exc_info=error)
        message = text("internal_error", language(interaction.locale))
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


def is_allowed(guild_id: int | None, settings: Settings) -> bool:
    """Commands only run in the allowed servers, never in direct messages."""
    return guild_id is not None and guild_id in settings.discord_guild_ids


def add_commands(tree: EsportCommandTree, *, with_ask: bool) -> None:
    @tree.command(name="roster", description=locale_str("Current roster of a team"))
    @app_commands.describe(
        team=TEAM_HELP,
        league=LEAGUE_HELP,
        staff=locale_str("Also show the staff (coaches, analysts, managers)"),
    )
    async def roster(
        interaction: discord.Interaction[EsportBot],
        team: str | None = None,
        league: str | None = None,
        staff: bool = False,
    ) -> None:
        await _reply(
            interaction,
            "roster",
            lambda bot, lang: roster_reply(bot.conn, bot.settings, lang, team, league, staff),
            team=team,
            league=league,
            staff=staff,
        )

    @tree.command(name="next", description=locale_str("Next match of a team"))
    @app_commands.describe(team=TEAM_HELP, league=LEAGUE_HELP)
    async def next_match(
        interaction: discord.Interaction[EsportBot],
        team: str | None = None,
        league: str | None = None,
    ) -> None:
        await _reply(
            interaction,
            "next",
            lambda bot, lang: next_match_reply(bot.conn, bot.settings, lang, team, league),
            team=team,
            league=league,
        )

    @tree.command(name="results", description=locale_str("Latest results of a team"))
    @app_commands.describe(
        team=TEAM_HELP, league=LEAGUE_HELP, limit=locale_str("Number of results (1 to 10)")
    )
    async def results(
        interaction: discord.Interaction[EsportBot],
        team: str | None = None,
        league: str | None = None,
        limit: app_commands.Range[int, 1, 10] = 5,
    ) -> None:
        await _reply(
            interaction,
            "results",
            lambda bot, lang: results_reply(bot.conn, bot.settings, lang, team, league, limit),
            team=team,
            league=league,
            limit=limit,
        )

    if not with_ask:
        return

    @tree.command(
        name="ask",
        description=locale_str(
            "Ask a question about League of Legends esports (questions are recorded)"
        ),
    )
    @app_commands.describe(question=locale_str("Your question, e.g. who coaches the team?"))
    async def ask(
        interaction: discord.Interaction[EsportBot],
        question: app_commands.Range[str, 1, MAX_QUESTION_LENGTH],
    ) -> None:
        service = interaction.client.ask_service
        assert service is not None  # The command only exists with the service.
        # Claude takes seconds: show "is thinking..." now, send the answer when ready.
        await interaction.response.defer(thinking=True)
        message = await service.answer(
            interaction.user.id, interaction.guild_id, question, language(interaction.locale)
        )
        await interaction.followup.send(message, allowed_mentions=NO_MENTIONS)


async def _reply(
    interaction: discord.Interaction[EsportBot],
    command: str,
    build: Callable[[EsportBot, Lang], str],
    **options: object,
) -> None:
    bot = interaction.client
    logger.info("/%s %s in server %s", command, options, interaction.guild_id)
    message = build(bot, language(interaction.locale))
    await interaction.response.send_message(message, allowed_mentions=NO_MENTIONS)
    record_request(
        bot.conn,
        RequestRecord(
            created_at=datetime.now(UTC),
            guild_id=interaction.guild_id,
            user_hash=pseudonymize(interaction.user.id, bot.user_hash_key),
            command=command,
            question=json.dumps(options, ensure_ascii=False),
            answer=message,
        ),
    )


def make_ask(settings: Settings, client: anthropic.Anthropic) -> AskFunction:
    """Answer with the agent, on a connection of its own: it runs in a worker thread, and a
    SQLite connection may only be used by the thread that opened it."""

    def ask(question: str) -> Answer:
        with closing(connect(settings.sqlite_path)) as conn:
            return Agent(client, conn, settings, max_tokens=ASK_MAX_TOKENS).ask(question)

    return ask


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level or "INFO", settings.log_file)
    try:
        token = require_secret(settings.discord_bot_token, "DISCORD_BOT_TOKEN")
        user_hash_key = require_secret(settings.discord_user_hash_key, "DISCORD_USER_HASH_KEY")
    except MissingSettingError as exc:
        raise SystemExit(str(exc)) from exc
    if not settings.discord_guild_ids:
        raise SystemExit("DISCORD_GUILD_IDS is empty: list the servers allowed to use the bot.")
    ask = None
    if settings.anthropic_api_key and settings.anthropic_api_key.get_secret_value():
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value())
        ask = make_ask(settings, client)
    else:
        logger.warning("No ANTHROPIC_API_KEY: /ask is disabled, the free commands still work")
    with closing(connect(settings.sqlite_path)) as conn:
        init_schema(conn)
        # log_handler=None: discord.py logs through the handlers set up above.
        EsportBot(settings, conn, user_hash_key, ask).run(token, log_handler=None)
