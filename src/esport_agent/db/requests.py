"""History of the Discord requests (`requests` table), which also backs the /ask quotas.

Users are stored as a pseudonym (see `bot.quotas.pseudonymize`), never as their Discord id.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True)
class RequestRecord:
    created_at: datetime
    """Timezone-aware; stored in UTC."""
    guild_id: int | None
    user_hash: str
    command: str
    """roster, next, results or ask."""
    question: str
    """The question asked with /ask, or the options of the other commands."""
    answer: str | None = None
    tools: tuple[str, ...] = field(default=())
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    error: str | None = None
    """Why no answer was given: "user_quota", "budget", or the failure of the agent."""


def record_request(conn: sqlite3.Connection, request: RequestRecord) -> None:
    with conn:
        conn.execute(
            "INSERT INTO requests (created_at, guild_id, user_hash, command, question, answer,"
            " tools, model, input_tokens, output_tokens, cost_usd, latency_ms, error)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _utc(request.created_at),
                str(request.guild_id) if request.guild_id is not None else None,
                request.user_hash,
                request.command,
                request.question,
                request.answer,
                json.dumps(list(request.tools)),
                request.model,
                request.input_tokens,
                request.output_tokens,
                request.cost_usd,
                request.latency_ms,
                request.error,
            ),
        )


def paid_questions_since(
    conn: sqlite3.Connection, user_hash: str, since: datetime
) -> list[datetime]:
    """Times of a user's /ask questions since `since` that called Claude (cost > 0), oldest
    first."""
    rows = conn.execute(
        "SELECT created_at FROM requests"
        " WHERE user_hash = ? AND command = 'ask' AND cost_usd > 0 AND created_at >= ?"
        " ORDER BY created_at",
        (user_hash, _utc(since)),
    ).fetchall()
    return [datetime.fromisoformat(row[0]) for row in rows]


def spent_since(conn: sqlite3.Connection, since: datetime) -> float:
    """Estimated Claude spending of all requests since `since`, in USD."""
    row = conn.execute(
        "SELECT coalesce(sum(cost_usd), 0) FROM requests WHERE created_at >= ?", (_utc(since),)
    ).fetchone()
    return float(row[0])


def delete_requests_before(conn: sqlite3.Connection, before: datetime) -> int:
    """Delete the requests older than `before` (retention); return how many were deleted."""
    with conn:
        cursor = conn.execute("DELETE FROM requests WHERE created_at < ?", (_utc(before),))
    return cursor.rowcount


def _utc(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")
