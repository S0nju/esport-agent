"""Schema and access to the local SQLite database."""

from esport_agent.db.connection import connect, init_schema
from esport_agent.db.records import MatchRecord, MatchSide, PlayerRecord, TeamRecord
from esport_agent.db.repository import replace_teams, upsert_matches

__all__ = [
    "MatchRecord",
    "MatchSide",
    "PlayerRecord",
    "TeamRecord",
    "connect",
    "init_schema",
    "replace_teams",
    "upsert_matches",
]
