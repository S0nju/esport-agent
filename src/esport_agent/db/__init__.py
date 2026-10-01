"""Schema and access to the local SQLite database."""

from esport_agent.db.connection import connect, init_schema
from esport_agent.db.records import MatchRecord, MatchSide, PlayerRecord, StaffRecord, TeamRecord
from esport_agent.db.repository import (
    delete_stale_matches,
    find_leagues,
    find_teams,
    find_teams_by_name_part,
    latest_league,
    next_match,
    recent_results,
    replace_teams,
    team_league_slugs,
    upsert_matches,
)

__all__ = [
    "MatchRecord",
    "MatchSide",
    "PlayerRecord",
    "StaffRecord",
    "TeamRecord",
    "connect",
    "delete_stale_matches",
    "find_leagues",
    "find_teams",
    "find_teams_by_name_part",
    "init_schema",
    "latest_league",
    "next_match",
    "recent_results",
    "replace_teams",
    "team_league_slugs",
    "upsert_matches",
]
