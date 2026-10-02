"""The tools exposed to Claude, and the dispatch from a tool call to them.

Each tool is a function of the database connection and its arguments that returns a
JSON-serializable dict. Times are returned in the configured time zone.
"""

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NotRequired, TypedDict
from zoneinfo import ZoneInfo

from esport_agent.config import Settings
from esport_agent.db import (
    MatchRecord,
    MatchSide,
    TeamRecord,
    find_leagues,
    find_teams,
    find_teams_by_name_part,
    latest_league,
    next_match,
    recent_results,
    team_league_slugs,
)

DEFAULT_RESULTS_LIMIT = 5
MAX_RESULTS_LIMIT = 20

LIVE_MATCH_GRACE = timedelta(hours=6)
"""How long after its start time a match not marked completed can still be the next one.

The database is only as fresh as the last sync: a match that started an hour ago may still
be stored as unstarted, while it is being played.
"""

ROLE_ORDER = ("top", "jungle", "mid", "bottom", "support")

ROLE_LABELS = {
    "top": "Top",
    "jungle": "Jungle",
    "mid": "Mid",
    "bottom": "Bot",
    "support": "Support",
}
"""How roles are shown to users. Players with any other source role ("none") are left out of
rosters: they are staff or inactive members, since a starter always has a role."""

INACTIVE_PLAYERS_NOTE = (
    "The source lists several players for some roles: the roster may include substitutes "
    "or inactive players."
)
MISSING_STARTERS_NOTE = (
    "No starter is listed for {roles}: the roster may be incomplete (players not announced "
    "yet, or substitutes only)."
)


class UnknownToolError(ValueError):
    """Claude asked for a tool that does not exist."""


@dataclass(frozen=True)
class ToolContext:
    """What the tools need besides their arguments."""

    default_team: str
    """Used when Claude does not name a team."""
    now: datetime
    tz: ZoneInfo
    """Time zone of the returned match times."""
    preferred_leagues: Sequence[str] = ()
    """League slugs, by priority, used to pick a team among several partial matches."""

    @classmethod
    def from_settings(cls, settings: Settings, now: datetime) -> ToolContext:
        tz = settings.tzinfo
        return cls(
            default_team=settings.default_team,
            now=now.astimezone(tz),
            tz=tz,
            preferred_leagues=tuple(settings.preferred_leagues),
        )


class TeamInfo(TypedDict):
    name: str
    code: str
    league: str | None
    """League of the team's latest match, or its home league if it has no match."""


class PlayerInfo(TypedDict):
    summoner_name: str
    real_name: str
    role: str
    """Top, Jungle, Mid, Bot or Support."""
    country: str | None
    substitute: bool


class StaffInfo(TypedDict):
    name: str
    real_name: str
    role: str
    """Coach, Analyst, Manager, Owner... as given by the source."""
    country: str | None


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


class ToolError(TypedDict):
    """Why a tool could not answer, with the teams the user may have meant."""

    error: str
    code: str
    """unknown_team, ambiguous_team, unknown_league or not_in_league: lets the Discord
    commands word the error in the user's language without parsing `error`."""
    candidates: list[str]


class LastKnownRoster(TypedDict):
    tournament: str
    ended: str | None
    """ISO date (YYYY-MM-DD) of the end of that tournament."""


class RosterResponse(TypedDict):
    team: TeamInfo
    active: bool
    """False when the team has no current roster: `players` and `staff` are then the ones
    it registered for its last tournament, described in `last_known_roster`."""
    last_known_roster: NotRequired[LastKnownRoster]
    players: list[PlayerInfo]
    """Starters first, then substitutes, each by role."""
    staff: list[StaffInfo]
    """Empty when the source gives no staff (teams without a Leaguepedia roster)."""
    note: NotRequired[str]
    """Only present when a role has several players that are not marked as substitutes, or
    no starter at all."""


class NextMatchResponse(TypedDict):
    team: TeamInfo
    next_match: MatchInfo | None


class RecentResultsResponse(TypedDict):
    team: TeamInfo
    results: list[ResultInfo]


def get_team_roster(
    conn: sqlite3.Connection,
    team: str,
    preferred_leagues: Sequence[str] = (),
    league: str | None = None,
) -> RosterResponse | ToolError:
    """Return the current players and staff of `team`, without role-less members.

    `league` picks the team of the organization playing there ("KC" + "LFL"). Players
    come with their country and a substitute flag when Leaguepedia knows the team; for
    the others (lolesports only), starters, substitutes and inactive players are mixed,
    so a `note` warns when a role has several players.
    """
    found = _resolve(conn, team, league, preferred_leagues)
    if isinstance(found, dict):
        return found
    resolved, _ = found
    players = sorted(
        (p for p in resolved.players if p.role in ROLE_LABELS),
        key=lambda p: (p.is_substitute, _role_rank(p.role), p.summoner_name),
    )
    response: RosterResponse = {
        "team": _team_info(conn, resolved),
        "active": resolved.roster_active,
        "players": [
            {
                "summoner_name": p.summoner_name,
                "real_name": f"{p.first_name} {p.last_name}".strip(),
                "role": ROLE_LABELS[p.role],
                "country": p.country,
                "substitute": p.is_substitute,
            }
            for p in players
        ],
        "staff": [
            {"name": s.name, "real_name": s.real_name, "role": s.role, "country": s.country}
            for s in resolved.staff
        ],
    }
    if not resolved.roster_active and resolved.roster_tournament:
        response["last_known_roster"] = {
            "tournament": resolved.roster_tournament,
            "ended": resolved.roster_date.isoformat() if resolved.roster_date else None,
        }
    starter_roles = [p.role for p in players if not p.is_substitute]
    notes = []
    if len(starter_roles) != len(set(starter_roles)):
        notes.append(INACTIVE_PLAYERS_NOTE)
    missing = [ROLE_LABELS[role] for role in ROLE_ORDER if role not in starter_roles]
    if missing:
        notes.append(MISSING_STARTERS_NOTE.format(roles=", ".join(missing)))
    if notes:
        response["note"] = " ".join(notes)
    return response


def get_team_next_match(
    conn: sqlite3.Connection,
    team: str,
    now: datetime,
    tz: ZoneInfo,
    preferred_leagues: Sequence[str] = (),
    league: str | None = None,
) -> NextMatchResponse | ToolError:
    """Return `team`'s next match, or the one being played, if any.

    `league` picks the team playing there and only considers the matches of that league.
    """
    found = _resolve(conn, team, league, preferred_leagues)
    if isinstance(found, dict):
        return found
    resolved, league_slugs = found
    match = next_match(conn, resolved.name, now - LIVE_MATCH_GRACE, league_slugs)
    return {
        "team": _team_info(conn, resolved),
        "next_match": _match_info(match, resolved.name, tz) if match else None,
    }


def get_team_recent_results(
    conn: sqlite3.Connection,
    team: str,
    limit: int,
    tz: ZoneInfo,
    preferred_leagues: Sequence[str] = (),
    league: str | None = None,
) -> RecentResultsResponse | ToolError:
    """Return `team`'s last `limit` results (clamped to 1-20), most recent first.

    `league` picks the team playing there and only returns the matches of that league.
    """
    found = _resolve(conn, team, league, preferred_leagues)
    if isinstance(found, dict):
        return found
    resolved, league_slugs = found
    limit = min(max(limit, 1), MAX_RESULTS_LIMIT)
    name = resolved.name
    matches = recent_results(conn, name, limit, league_slugs)
    return {
        "team": _team_info(conn, resolved),
        "results": [_result_info(match, name, tz) for match in matches],
    }


def execute_tool(
    conn: sqlite3.Connection,
    name: str,
    tool_input: Mapping[str, object],
    ctx: ToolContext,
) -> str:
    """Run the `name` tool and return its result serialized as JSON for Claude.

    If Claude does not specify a team, `ctx.default_team` is used.
    """
    team = tool_input.get("team")
    if not isinstance(team, str) or not team.strip():
        team = ctx.default_team
    league = tool_input.get("league")
    if not isinstance(league, str) or not league.strip():
        league = None
    leagues = ctx.preferred_leagues

    result: object
    match name:
        case "get_team_roster":
            result = get_team_roster(conn, team, leagues, league)
        case "get_team_next_match":
            result = get_team_next_match(conn, team, ctx.now, ctx.tz, leagues, league)
        case "get_team_recent_results":
            limit = tool_input.get("limit")
            # bool is a subclass of int, but `true` is not a meaningful limit.
            if not isinstance(limit, int) or isinstance(limit, bool):
                limit = DEFAULT_RESULTS_LIMIT
            result = get_team_recent_results(conn, team, limit, ctx.tz, leagues, league)
        case _:
            raise UnknownToolError(f"Unknown tool: {name}")
    return json.dumps(result, ensure_ascii=False)


def _resolve(
    conn: sqlite3.Connection, team: str, league: str | None, preferred_leagues: Sequence[str]
) -> tuple[TeamRecord, set[str]] | ToolError:
    """Resolve the team and, if given, the league slugs the user asked about."""
    if league is None:
        resolved = _resolve_team(conn, team, preferred_leagues)
        return (resolved, set()) if isinstance(resolved, TeamRecord) else resolved
    league_slugs = find_leagues(conn, league)
    if not league_slugs:
        return {
            "error": f"No synced league matches {league!r}.",
            "code": "unknown_league",
            "candidates": [],
        }
    resolved = _resolve_team_in_league(conn, team, league, league_slugs)
    return (resolved, league_slugs) if isinstance(resolved, TeamRecord) else resolved


def _resolve_team_in_league(
    conn: sqlite3.Connection, query: str, league: str, league_slugs: set[str]
) -> TeamRecord | ToolError:
    """Pick the team matching `query` that plays in one of `league_slugs`.

    If the team the user named does not play there, look for another team of the same
    organization that does, i.e. a team whose name contains its name: "KC" in the LFL is
    Karmine Corp Blue.
    """

    def plays_there(team: TeamRecord) -> bool:
        return bool(team_league_slugs(conn, team.name) & league_slugs)

    teams, exact = find_teams(conn, query)
    candidates = [t for t in teams if plays_there(t)]
    if exact and candidates:
        return candidates[0]
    if exact:
        same_organization: dict[str, TeamRecord] = {}
        for team in teams:
            for other in find_teams_by_name_part(conn, team.name):
                if plays_there(other):
                    same_organization.setdefault(other.id, other)
        candidates = list(same_organization.values())
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        return {
            "error": f"No team matching {query!r} plays in {league!r}.",
            "code": "not_in_league",
            "candidates": [],
        }
    return {
        "error": f"Several teams match {query!r} in {league!r}: ask the user which one.",
        "code": "ambiguous_team",
        "candidates": [_describe(conn, t) for t in candidates],
    }


def _resolve_team(
    conn: sqlite3.Connection, query: str, preferred_leagues: Sequence[str]
) -> TeamRecord | ToolError:
    """Pick the team the user most likely means, or explain why none could be picked.

    Exact matches (name, code or slug) are ranked and the best one wins, since homonyms are
    mostly old or amateur teams. For a partial name, a single relevant team (active, in a
    league) is accepted; among several, the only one playing in the first preferred league
    that has any of them wins ("Vitality" gives the LEC team, not its academy). Otherwise the
    candidates are returned.
    """
    teams, exact = find_teams(conn, query)
    if exact:
        return teams[0]
    relevant = [t for t in teams if t.status == "active" and t.home_league] or teams
    if len(relevant) == 1:
        return relevant[0]
    if not relevant:
        return {"error": f"No team matches {query!r}.", "code": "unknown_team", "candidates": []}
    for league in preferred_leagues:
        in_league = [t for t in relevant if league in team_league_slugs(conn, t.name)]
        if len(in_league) == 1:
            return in_league[0]
        if in_league:
            relevant = in_league
            break
    return {
        "error": f"Several teams match {query!r}: ask the user which one, or pick one.",
        "code": "ambiguous_team",
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
