import asyncio
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import anthropic
import httpx2
import pytest

from esport_agent.agent import AgentError, Answer
from esport_agent.bot.ask import (
    DISCORD_MESSAGE_LIMIT,
    QUESTION_RESERVE_USD,
    AskService,
    day_start,
    format_answer,
    pseudonymize,
)
from esport_agent.config import Settings
from esport_agent.db import RequestRecord, record_request
from esport_agent.usage import Usage

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 10, 2, 18, tzinfo=UTC)  # 20:00 in Paris
USER = 123456789
KEY = "secret-key"


def usage(input_tokens: int = 4000, output_tokens: int = 200) -> Usage:
    return Usage(
        model="claude-haiku-4-5-20251001",
        calls=2,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


class FakeAgent:
    def __init__(self, answer: Answer | Exception | None = None) -> None:
        self.answer = answer or Answer("**KC** plays tonight.", usage(), ("get_team_next_match",))
        self.questions: list[str] = []

    def __call__(self, question: str) -> Answer:
        self.questions.append(question)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def make_service(
    conn: sqlite3.Connection, agent: FakeAgent, now: datetime = NOW, **settings: object
) -> AskService:
    config = Settings(_env_file=None, **settings)  # type: ignore[arg-type]
    return AskService(conn, config, KEY, agent, clock=lambda: now)


def past_question(
    conn: sqlite3.Connection,
    when: datetime,
    cost: float,
    user: int = USER,
    command: str = "ask",
) -> None:
    record_request(
        conn,
        RequestRecord(
            created_at=when,
            guild_id=1,
            user_hash=pseudonymize(user, KEY),
            command=command,
            question="?",
            cost_usd=cost,
        ),
    )


def rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM requests ORDER BY id").fetchall()


async def test_answer_is_quoted_counted_and_recorded(conn: sqlite3.Connection) -> None:
    agent = FakeAgent()
    service = make_service(conn, agent)

    message = await service.answer(USER, 42, "When  does\nKC play?", "en")

    assert message == ("> When does KC play?\n**KC** plays tonight.\n-# Questions left: 4/5")
    assert agent.questions == ["When  does\nKC play?"]
    (row,) = rows(conn)
    assert row["user_hash"] == pseudonymize(USER, KEY)
    assert str(USER) not in row["user_hash"]
    assert (row["guild_id"], row["command"], row["tools"]) == (
        "42",
        "ask",
        '["get_team_next_match"]',
    )
    assert row["cost_usd"] == pytest.approx(0.005)  # 4,000 in + 200 out tokens with Haiku
    assert row["error"] is None


async def test_user_quota_is_a_rolling_24_hour_window(conn: sqlite3.Connection) -> None:
    for _ in range(3):
        past_question(conn, NOW - timedelta(hours=25), 0.005)  # No longer counted.
    oldest = NOW - timedelta(hours=20)
    past_question(conn, oldest, 0.005)
    for _ in range(3):
        past_question(conn, NOW - timedelta(hours=1), 0.005)
    past_question(conn, NOW, 0.0, command="roster")  # Free commands do not count.
    past_question(conn, NOW, 0.005, user=999)  # Neither do other users.
    agent = FakeAgent()
    service = make_service(conn, agent)

    last = await service.answer(USER, 1, "Q5", "fr")
    refused = await service.answer(USER, 1, "Q6", "en")

    back = int((oldest + timedelta(hours=24)).timestamp())  # In 4 hours.
    assert last.endswith(f"-# Nouvelle question disponible <t:{back}:R>")
    assert refused.startswith(
        f"You have no questions left for now. Next question available <t:{back}:R>."
    )
    assert agent.questions == ["Q5"]
    assert rows(conn)[-1]["error"] == "user_quota"


async def test_a_single_question_quota_comes_back_after_24_hours(
    conn: sqlite3.Connection,
) -> None:
    service = make_service(conn, FakeAgent(), ask_questions_per_user_per_day=1)

    message = await service.answer(USER, 1, "Q", "fr")

    back = int((NOW + timedelta(hours=24)).timestamp())
    assert message.endswith(f"-# Nouvelle question disponible <t:{back}:R>")


async def test_daily_budget_keeps_a_reserve(conn: sqlite3.Connection) -> None:
    past_question(conn, NOW - timedelta(hours=1), 0.49, user=999)
    agent = FakeAgent()
    service = make_service(conn, agent, ask_daily_budget_usd=0.50)

    message = await service.answer(USER, 1, "Q", "en")

    assert message.startswith("The bot has reached its question limit for today")
    assert agent.questions == []
    assert rows(conn)[-1]["error"] == "budget"


async def test_budget_resets_at_midnight_in_the_configured_time_zone(
    conn: sqlite3.Connection,
) -> None:
    past_question(conn, datetime(2026, 10, 1, 21, 59, tzinfo=UTC), 0.49, user=999)
    agent = FakeAgent()

    await make_service(conn, agent).answer(USER, 1, "Q", "en")

    assert agent.questions == ["Q"]


async def test_one_question_at_a_time_per_user(conn: sqlite3.Connection) -> None:
    started, release = threading.Event(), threading.Event()

    class SlowAgent(FakeAgent):
        def __call__(self, question: str) -> Answer:
            started.set()
            release.wait(timeout=5)
            return super().__call__(question)

    service = make_service(conn, SlowAgent())
    first = asyncio.create_task(service.answer(USER, 1, "Q1", "en"))
    await asyncio.to_thread(started.wait, 5)

    second = await service.answer(USER, 1, "Q2", "en")
    release.set()

    assert second == "Your previous question is still being answered, wait for it."
    assert (await first).startswith("> Q1")
    assert len(rows(conn)) == 1


async def test_failures_are_recorded_with_what_they_cost(conn: sqlite3.Connection) -> None:
    failing = make_service(conn, FakeAgent(AgentError("No final answer", usage())))
    api_error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.test"))
    unreachable = make_service(conn, FakeAgent(api_error))

    assert await failing.answer(USER, 1, "Q", "fr") == (
        "Je n'ai pas pu répondre à cette question, réessaie plus tard."
    )
    await unreachable.answer(USER, 1, "Q", "fr")

    first, second = rows(conn)
    assert (first["error"], first["cost_usd"]) == ("No final answer", pytest.approx(0.005))
    assert (second["cost_usd"], second["answer"]) == (0, None)


async def test_unknown_model_price_still_counts(conn: sqlite3.Connection) -> None:
    unknown = Usage(model="claude-future", calls=1, input_tokens=10, output_tokens=10)
    service = make_service(conn, FakeAgent(Answer("Hi", unknown)))

    await service.answer(USER, 1, "Q", "en")

    assert rows(conn)[0]["cost_usd"] == QUESTION_RESERVE_USD


async def test_old_requests_are_deleted(conn: sqlite3.Connection) -> None:
    past_question(conn, NOW - timedelta(days=91), 0.005)
    past_question(conn, NOW - timedelta(days=89), 0.005)

    await make_service(conn, FakeAgent()).answer(USER, 1, "Q", "en")

    assert len(rows(conn)) == 2  # The 89-day-old one and the new one.


def test_long_answers_fit_in_a_discord_message() -> None:
    message = format_answer("Q", "x" * 5000, "Questions left: 4/5")

    assert len(message) <= DISCORD_MESSAGE_LIMIT
    assert message.endswith("…\n-# Questions left: 4/5")


def test_pseudonyms_are_stable_and_depend_on_the_key() -> None:
    assert pseudonymize(USER, KEY) == pseudonymize(USER, KEY)
    assert pseudonymize(USER, KEY) != pseudonymize(USER, "other-key")
    assert pseudonymize(USER, KEY) != pseudonymize(USER + 1, KEY)


def test_day_starts_at_local_midnight() -> None:
    assert day_start(datetime(2026, 10, 1, 22, 30, tzinfo=UTC), PARIS) == datetime(
        2026, 10, 2, tzinfo=PARIS
    )
