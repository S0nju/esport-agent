"""Schema and access to the local SQLite database."""

from esport_agent.db.connection import connect, init_schema
from esport_agent.db.records import MatchRecord, MatchSide, PlayerRecord, TeamRecord
from esport_agent.db.repository import (
    delete_stale_matches,
    find_teams,
    latest_league,
    next_match,
    recent_results,
    replace_teams,
    upsert_matches,
)

__all__ = [
    "MatchRecord",
    "MatchSide",
    "PlayerRecord",
    "TeamRecord",
    "connect",
    "delete_stale_matches",
    "find_teams",
    "init_schema",
    "latest_league",
    "next_match",
    "recent_results",
    "replace_teams",
    "upsert_matches",
]
