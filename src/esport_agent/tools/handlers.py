"""The tools exposed to Claude, and the dispatch from a tool call to them.

Each tool is a function of the database connection and its arguments that returns a
JSON-serializable dict. Times are returned in the configured time zone.
"""

import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import TypedDict
from zoneinfo import ZoneInfo

from esport_agent.db import (
    MatchRecord,
    MatchSide,
    TeamRecord,
    find_teams,
    latest_league,
    next_match,
    recent_results,
)

DEFAULT_RESULTS_LIMIT = 5
MAX_RESULTS_LIMIT = 20

LIVE_MATCH_GRACE = timedelta(hours=6)
"""How long after its start time a match not marked completed can still be the next one.

The database is only as fresh as the last sync: a match that started an hour ago may still
be stored as unstarted, while it is being played.
"""

ROLE_ORDER = ("top", "jungle", "mid", "bottom", "support")


class UnknownToolError(ValueError):
    """Claude asked for a tool that does not exist."""


class TeamInfo(TypedDict):
    name: str
    code: str
    league: str | None
    """League of the team's latest match, or its home league if it has no match."""


class PlayerInfo(TypedDict):
    summoner_name: str
    first_name: str
    last_name: str
    role: str


class MatchInfo(TypedDict):
    start_time: str
    """ISO 8601, in the configured time zone."""
    weekday: str
    state: str
    league: str
    stage: str | None
    best_of: int | None
    opponent: str


class ResultInfo(MatchInfo):
    score: str
    """From the team's point of view, e.g. "3-1"."""
    result: str | None
    """"win" or "loss"."""


class TeamNotFound(TypedDict):
    error: str
    candidates: list[str]


class RosterResponse(TypedDict):
    team: TeamInfo
    players: list[PlayerInfo]


class NextMatchResponse(TypedDict):
    team: TeamInfo
    next_match: MatchInfo | None


class RecentResultsResponse(TypedDict):
    team: TeamInfo
    results: list[ResultInfo]


def get_team_roster(conn: sqlite3.Connection, team: str) -> RosterResponse | TeamNotFound:
    """Return the current roster of `team`, main roles first."""
    resolved = _resolve_team(conn, team)
    if not isinstance(resolved, TeamRecord):
        return resolved
    players = sorted(resolved.players, key=lambda p: (_role_rank(p.role), p.summoner_name))
    return {
        "team": _team_info(conn, resolved),
        "players": [
            {
                "summoner_name": p.summoner_name,
                "first_name": p.first_name,
                "last_name": p.last_name,
                "role": p.role,
            }
            for p in players
        ],
    }


def get_team_next_match(
    conn: sqlite3.Connection, team: str, now: datetime, tz: ZoneInfo
) -> NextMatchResponse | TeamNotFound:
    """Return `team`'s next match, or the one being played, if any."""
    resolved = _resolve_team(conn, team)
    if not isinstance(resolved, TeamRecord):
        return resolved
    match = next_match(conn, resolved.name, since=now - LIVE_MATCH_GRACE)
    return {
        "team": _team_info(conn, resolved),
        "next_match": _match_info(match, resolved.name, tz) if match else None,
    }


def get_team_recent_results(
    conn: sqlite3.Connection, team: str, limit: int, tz: ZoneInfo
) -> RecentResultsResponse | TeamNotFound:
    """Return `team`'s last `limit` results (clamped to 1-20), most recent first."""
    resolved = _resolve_team(conn, team)
    if not isinstance(resolved, TeamRecord):
        return resolved
    limit = min(max(limit, 1), MAX_RESULTS_LIMIT)
    name = resolved.name
    return {
        "team": _team_info(conn, resolved),
        "results": [_result_info(match, name, tz) for match in recent_results(conn, name, limit)],
    }


def execute_tool(
    conn: sqlite3.Connection,
    name: str,
    tool_input: Mapping[str, object],
    *,
    default_team: str,
    now: datetime,
    tz: ZoneInfo,
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
            result = get_team_next_match(conn, team, now, tz)
        case "get_team_recent_results":
            limit = tool_input.get("limit")
            # bool is a subclass of int, but `true` is not a meaningful limit.
            if not isinstance(limit, int) or isinstance(limit, bool):
                limit = DEFAULT_RESULTS_LIMIT
            result = get_team_recent_results(conn, team, limit, tz)
        case _:
            raise UnknownToolError(name)
    return json.dumps(result, ensure_ascii=False)


def _resolve_team(conn: sqlite3.Connection, query: str) -> TeamRecord | TeamNotFound:
    """Pick the team the user most likely means, or explain why none could be picked.

    Exact matches (name, code or slug) are ranked and the best one wins, since homonyms are
    mostly old or amateur teams. A partial name match is only accepted when a single
    relevant team (active, in a league) matches; otherwise the candidates are returned.
    """
    teams, exact = find_teams(conn, query)
    if exact:
        return teams[0]
    relevant = [t for t in teams if t.status == "active" and t.home_league] or teams
    if len(relevant) == 1:
        return relevant[0]
    if not relevant:
        return {"error": f"No team matches {query!r}.", "candidates": []}
    return {
        "error": f"Several teams match {query!r}: ask the user which one, or pick one.",
        "candidates": [_describe(conn, t) for t in relevant],
    }


def _league(conn: sqlite3.Connection, team: TeamRecord) -> str | None:
    return latest_league(conn, team.name) or team.home_league


def _describe(conn: sqlite3.Connection, team: TeamRecord) -> str:
    return f"{team.name} ({team.code}, {_league(conn, team) or 'no league'})"


def _role_rank(role: str) -> int:
    return ROLE_ORDER.index(role) if role in ROLE_ORDER else len(ROLE_ORDER)


def _team_info(conn: sqlite3.Connection, team: TeamRecord) -> TeamInfo:
    return {"name": team.name, "code": team.code, "league": _league(conn, team)}


def _sides(match: MatchRecord, team_name: str) -> tuple[MatchSide, MatchSide]:
    """Return (team, opponent) for `team_name`'s point of view."""
    if match.team1.name.casefold() == team_name.casefold():
        return match.team1, match.team2
    return match.team2, match.team1


def _match_info(match: MatchRecord, team_name: str, tz: ZoneInfo) -> MatchInfo:
    local = match.start_time.astimezone(tz)
    _, opponent = _sides(match, team_name)
    return {
        "start_time": local.isoformat(),
        "weekday": local.strftime("%A"),
        "state": match.state,
        "league": match.league_name,
        "stage": match.block_name,
        "best_of": match.best_of,
        "opponent": opponent.name,
    }


def _result_info(match: MatchRecord, team_name: str, tz: ZoneInfo) -> ResultInfo:
    own, opponent = _sides(match, team_name)
    return {
        **_match_info(match, team_name, tz),
        "score": f"{own.game_wins or 0}-{opponent.game_wins or 0}",
        "result": own.outcome,
    }
