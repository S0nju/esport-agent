"""Client for the unofficial lolesports API (leagues, teams, schedule).

This API is undocumented and may change without notice: everything that depends on it
stays isolated in this module, and every response is validated against the models below
so that a format change fails loudly with `LolesportsFormatError`. It is only called by
`sync.py`, never by the tools.
"""

import logging
from datetime import datetime

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic.alias_generators import to_camel

logger = logging.getLogger(__name__)

BASE_URL = "https://esports-api.lolesports.com/persisted/gw"
DEFAULT_LOCALE = "en-US"


class LolesportsFormatError(RuntimeError):
    """The API returned a payload that does not match the expected format."""


class _Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)


class League(_Model):
    id: str
    slug: str
    name: str
    region: str


class HomeLeague(_Model):
    name: str
    region: str


class Player(_Model):
    id: str
    summoner_name: str
    first_name: str
    last_name: str
    role: str
    """One of `top`, `jungle`, `mid`, `bottom`, `support` or `none`."""


class Team(_Model):
    id: str
    slug: str
    name: str
    code: str
    status: str
    """`active` or `archived`."""
    home_league: HomeLeague | None
    players: list[Player]


class MatchResult(_Model):
    outcome: str | None
    """`win` or `loss` once the match is over, `None` while it is being played."""
    game_wins: int


class MatchTeam(_Model):
    name: str
    """`TBD` when the opponent is not known yet."""
    code: str
    result: MatchResult | None


class Strategy(_Model):
    type: str
    count: int


class Match(_Model):
    id: str
    teams: list[MatchTeam]
    strategy: Strategy


class EventLeague(_Model):
    name: str
    slug: str


class Event(_Model):
    start_time: datetime
    state: str
    """`unstarted`, `inProgress` or `completed`."""
    type: str
    block_name: str | None
    league: EventLeague
    match: Match | None = None


class SchedulePages(_Model):
    older: str | None
    newer: str | None


class Schedule(_Model):
    pages: SchedulePages
    events: list[Event]


class _LeaguesData(_Model):
    leagues: list[League]


class _LeaguesResponse(_Model):
    data: _LeaguesData


class _TeamsData(_Model):
    teams: list[Team]


class _TeamsResponse(_Model):
    data: _TeamsData


class _ScheduleData(_Model):
    schedule: Schedule


class _ScheduleResponse(_Model):
    data: _ScheduleData


class LolesportsClient:
    """Thin typed wrapper around the lolesports endpoints used by the sync."""

    def __init__(self, http: httpx.Client, api_key: str, locale: str = DEFAULT_LOCALE) -> None:
        self._http = http
        self._api_key = api_key
        self._locale = locale

    def get_leagues(self) -> list[League]:
        """Return every league known to lolesports."""
        return self._get("getLeagues", {}, _LeaguesResponse).data.leagues

    def get_teams(self, slug: str | None = None) -> list[Team]:
        """Return the team with the given slug, or every team (with rosters) if omitted."""
        params = {"id": slug} if slug else {}
        return self._get("getTeams", params, _TeamsResponse).data.teams

    def get_schedule(self, league_id: str, page_token: str | None = None) -> Schedule:
        """Return one page of a league's schedule, around the current date by default.

        The API only accepts one league per call. Use `Schedule.pages` tokens to page
        towards older or newer events.
        """
        params = {"leagueId": league_id}
        if page_token:
            params["pageToken"] = page_token
        return self._get("getSchedule", params, _ScheduleResponse).data.schedule

    def _get[T: BaseModel](self, endpoint: str, params: dict[str, str], model: type[T]) -> T:
        logger.debug("GET %s %s", endpoint, params)
        response = self._http.get(
            f"{BASE_URL}/{endpoint}",
            params={"hl": self._locale, **params},
            headers={"x-api-key": self._api_key},
        )
        response.raise_for_status()
        try:
            return model.model_validate_json(response.content)
        except ValidationError as exc:
            raise LolesportsFormatError(f"Unexpected {endpoint} payload: {exc}") from exc
