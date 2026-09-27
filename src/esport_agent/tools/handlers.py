"""Dispatch between the tool picked by Claude and the local database reads.

Return types are provisional: they will be adjusted once the actual format of the
Leaguepedia and lolesports data has been checked.
"""

import json
import sqlite3
from collections.abc import Mapping
from typing import TypedDict

DEFAULT_RESULTS_LIMIT = 5


class UnknownToolError(ValueError):
    """Claude asked for a tool that does not exist."""


class RosterEntry(TypedDict):
    player: str
    role: str


class Match(TypedDict):
    start_time_utc: str
    team1: str
    team2: str
    tournament: str
    best_of: int


class MatchResult(TypedDict):
    start_time_utc: str
    team1: str
    team2: str
    tournament: str
    team1_score: int
    team2_score: int
    winner: str


def get_team_roster(conn: sqlite3.Connection, team: str) -> list[RosterEntry]:
    """Return `team`'s current roster from the local database (empty list if unknown)."""
    raise NotImplementedError


def get_team_next_match(conn: sqlite3.Connection, team: str) -> Match | None:
    """Return `team`'s next scheduled match, or `None` if there is none."""
    raise NotImplementedError


def get_team_recent_results(
    conn: sqlite3.Connection, team: str, limit: int = DEFAULT_RESULTS_LIMIT
) -> list[MatchResult]:
    """Return `team`'s last `limit` results, most recent first."""
    raise NotImplementedError


def execute_tool(
    conn: sqlite3.Connection,
    name: str,
    tool_input: Mapping[str, object],
    default_team: str,
) -> str:
    """Run the `name` tool and return its result serialized as JSON for Claude.

    If Claude does not specify a team, `default_team` is used.
    """
    team = tool_input.get("team")
    if not isinstance(team, str) or not team.strip():
        team = default_team

    result: object
    match name:
        case "get_team_roster":
            result = get_team_roster(conn, team)
        case "get_team_next_match":
            result = get_team_next_match(conn, team)
        case "get_team_recent_results":
            limit = tool_input.get("limit", DEFAULT_RESULTS_LIMIT)
            if not isinstance(limit, int):
                limit = DEFAULT_RESULTS_LIMIT
            result = get_team_recent_results(conn, team, limit)
        case _:
            raise UnknownToolError(name)
    return json.dumps(result, ensure_ascii=False)
