"""Source-agnostic records written to and read from the local database."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PlayerRecord:
    id: str
    summoner_name: str
    first_name: str
    last_name: str
    role: str


@dataclass(frozen=True)
class TeamRecord:
    id: str
    slug: str
    name: str
    code: str
    status: str
    home_league: str | None
    players: tuple[PlayerRecord, ...]


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
