"""Source-agnostic records written to and read from the local database."""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class PlayerRecord:
    id: str
    summoner_name: str
    first_name: str
    last_name: str
    """Empty when the source only gives a full name (Leaguepedia), kept in `first_name`."""
    role: str
    """top, jungle, mid, bottom, support or none."""
    country: str | None = None
    is_substitute: bool = False


@dataclass(frozen=True)
class StaffRecord:
    name: str
    real_name: str
    role: str
    """Coach, Analyst, Manager, Owner... as given by the source."""
    country: str | None = None


@dataclass(frozen=True)
class TeamRecord:
    id: str
    slug: str
    name: str
    code: str
    status: str
    home_league: str | None
    players: tuple[PlayerRecord, ...]
    staff: tuple[StaffRecord, ...] = ()
    leaguepedia_name: str | None = None
    """Name of the team on Leaguepedia, when its roster comes from there."""
    roster_active: bool = True
    """False for a last known roster: the team has no current roster on Leaguepedia."""
    roster_tournament: str | None = None
    """For a last known roster, the tournament it was registered for."""
    roster_date: date | None = None
    """For a last known roster, the end date of that tournament."""


@dataclass(frozen=True)
class MatchSide:
    name: str
    code: str
    outcome: str | None
    game_wins: int | None


@dataclass(frozen=True)
class MatchRecord:
    id: str
    start_time: datetime
    """Timezone-aware; stored in UTC."""
    state: str
    league_slug: str
    league_name: str
    block_name: str | None
    best_of: int | None
    team1: MatchSide
    team2: MatchSide
