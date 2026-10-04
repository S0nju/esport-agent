"""/ask: free-text questions answered by the agent, within quotas, with a request history.

Each question costs real money (Claude), so two limits are checked before calling the
agent, from the `requests` table:

- per user: `ASK_QUESTIONS_PER_USER_PER_DAY` answers that called Claude over the last 24
  hours: each question counts for 24 hours, then its slot comes back. A rolling window is
  the same in every time zone and cannot be gamed. Questions are counted whatever their
  size, since a question's cost is bounded (`MAX_QUESTION_LENGTH`, `ASK_MAX_TOKENS`, the
  agent's tool rounds);
- for everyone: `ASK_DAILY_BUDGET_USD` of estimated spending since midnight in `TIMEZONE`
  (the owner's day). A question's cost is only known once answered, so each question being
  answered keeps `QUESTION_RESERVE_USD` of the budget aside until then.

The agent is slow (seconds) and blocking, so it runs in a worker thread, with its own
SQLite connection, while the bot keeps answering other commands.
"""

import asyncio
import hashlib
import hmac
import logging
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import anthropic

from esport_agent.agent import AgentError, Answer
from esport_agent.bot.translations import Lang, text
from esport_agent.config import Settings
from esport_agent.db import (
    RequestRecord,
    delete_requests_before,
    paid_questions_since,
    record_request,
    spent_since,
)
from esport_agent.usage import Usage

logger = logging.getLogger(__name__)

QUESTION_RESERVE_USD = 0.02
"""Budget kept aside for each question being answered (a question usually costs about
$0.005 with Claude Haiku 4.5; several tool rounds cost more)."""
MAX_QUESTION_LENGTH = 300
ASK_MAX_TOKENS = 1024
"""Output limit of each Claude call for /ask: a Discord message holds 2,000 characters,
about 600 tokens, so this bounds the cost of an answer without cutting normal ones."""
DISCORD_MESSAGE_LIMIT = 2000
QUOTA_WINDOW = timedelta(hours=24)

type AskFunction = Callable[[str], Answer]
"""Answers a question with the agent; called in a worker thread."""


def pseudonymize(user_id: int, key: str) -> str:
    """Return a stable pseudonym for a Discord user.

    A keyed hash (HMAC-SHA256): the same user always gets the same pseudonym, so quotas
    work, but without the key nobody can tell which Discord account it is, not even by
    hashing every possible id.
    """
    return hmac.new(key.encode(), str(user_id).encode(), hashlib.sha256).hexdigest()


def day_start(now: datetime, tz: ZoneInfo) -> datetime:
    """Midnight of `now`'s day in `tz`: quotas reset then."""
    return now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)


class AskService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        settings: Settings,
        user_hash_key: str,
        ask: AskFunction,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._conn = conn
        self._settings = settings
        self._key = user_hash_key
        self._ask = ask
        self._clock = clock
        self._in_flight: set[str] = set()
        """Users whose question is being answered: one question at a time per user."""

    async def answer(self, user_id: int, guild_id: int | None, question: str, lang: Lang) -> str:
        """Check the quotas, ask the agent and return the Discord message to send."""
        now = self._clock()
        user = pseudonymize(user_id, self._key)
        retention = timedelta(days=self._settings.request_retention_days)
        delete_requests_before(self._conn, now - retention)
        if user in self._in_flight:
            return text("ask_in_progress", lang)

        limit = self._settings.ask_questions_per_user_per_day
        counted = paid_questions_since(self._conn, user, now - QUOTA_WINDOW)
        used = len(counted)
        refusal = None
        if used >= limit:
            # A slot comes back when the oldest question that still counts is 24 hours old.
            when = _relative(counted[used - limit] + QUOTA_WINDOW)
            refusal, message = "user_quota", text("user_quota", lang, when=when)
        elif self._over_budget(day_start(now, self._settings.tzinfo)):
            refusal, message = "budget", text("budget_reached", lang)
        if refusal:
            logger.info("/ask refused (%s) in server %s", refusal, guild_id)
            self._record(now, guild_id, user, question, error=refusal)
            return message

        self._in_flight.add(user)
        started = time.monotonic()
        try:
            answer = await asyncio.to_thread(self._ask, question)
        except (AgentError, anthropic.APIError) as exc:
            # Expected failures (no answer, API down, credits used up): the user gets a short
            # message, the paid calls are recorded so that they count in the budget.
            logger.error("/ask failed: %s", exc)
            usage = exc.usage if isinstance(exc, AgentError) else None
            self._record(
                now,
                guild_id,
                user,
                question,
                usage=usage,
                error=str(exc),
                started=started,
            )
            return text("ask_failed", lang)
        finally:
            self._in_flight.discard(user)

        self._record(
            now,
            guild_id,
            user,
            question,
            answer=answer.text,
            usage=answer.usage,
            tools=answer.tools,
            started=started,
        )
        logger.info("/ask answered in server %s: %s", guild_id, answer.usage.summary())
        remaining = max(limit - used - 1, 0)
        if remaining:
            footer = text("ask_footer", lang, remaining=remaining, limit=limit)
        else:
            # This answer used the last slot: one comes back 24 hours after the oldest
            # question still counted (this one, if it is the only one).
            oldest = counted[0] if counted else now
            footer = text("ask_footer_last", lang, when=_relative(oldest + QUOTA_WINDOW))
        return format_answer(question, answer.text, footer)

    def _over_budget(self, since: datetime) -> bool:
        reserved = (len(self._in_flight) + 1) * QUESTION_RESERVE_USD
        return spent_since(self._conn, since) + reserved > self._settings.ask_daily_budget_usd

    def _record(
        self,
        now: datetime,
        guild_id: int | None,
        user: str,
        question: str,
        *,
        answer: str | None = None,
        usage: Usage | None = None,
        tools: tuple[str, ...] = (),
        error: str | None = None,
        started: float | None = None,
    ) -> None:
        cost = usage.estimated_cost_usd if usage else None
        if cost is None and usage:
            # An unknown model price must still count against the budget.
            cost = QUESTION_RESERVE_USD
        cost = cost or 0.0
        record_request(
            self._conn,
            RequestRecord(
                created_at=now,
                guild_id=guild_id,
                user_hash=user,
                command="ask",
                question=question,
                answer=answer,
                tools=tools,
                model=usage.model if usage else None,
                input_tokens=usage.input_tokens if usage else 0,
                output_tokens=usage.output_tokens if usage else 0,
                cost_usd=cost,
                latency_ms=round((time.monotonic() - started) * 1000) if started else 0,
                error=error,
            ),
        )


def _relative(moment: datetime) -> str:
    """A Discord timestamp shown as "in 6 hours" in each user's language, kept up to date by
    Discord (which shows "in a day" from about 22 hours)."""
    return f"<t:{int(moment.timestamp())}:R>"


def format_answer(question: str, answer: str, footer: str) -> str:
    """The question quoted, the answer, then the quota footer, within Discord's limit."""
    quoted = "> " + " ".join(question.split())
    room = DISCORD_MESSAGE_LIMIT - len(quoted) - len(footer) - len("\n\n-# ")
    if len(answer) > room:
        answer = answer[: room - 1].rstrip() + "…"
    return f"{quoted}\n{answer}\n-# {footer}"
