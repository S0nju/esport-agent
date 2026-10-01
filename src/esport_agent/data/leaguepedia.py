"""Client for Leaguepedia's Cargo API (rosters, staff, countries).

Leaguepedia rate limits are very low: about 5 requests per minute for a logged-in bot
account, and far less anonymously. This client paces its requests, waits and retries when
the server says `ratelimited`, and handles pagination itself (mwrogue's own pagination
fires page requests back to back). It is only called by `sync.py`, never by the tools.
"""

import html
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from esport_agent.config import Settings

logger = logging.getLogger(__name__)

MIN_REQUEST_INTERVAL_SECONDS = 15.0
"""Measured limit: about 5 requests per minute; one every 15 s never got refused."""
RATE_LIMIT_WAIT_SECONDS = 60.0
"""The server counts requests over about a minute: after a refusal, wait for a new window."""
MAX_RATE_LIMIT_RETRIES = 4
PAGE_SIZE = 500
"""Rows asked per request (Cargo's maximum for a logged-in account)."""
TEAMS_PER_QUERY = 25
"""Team names per `IN (...)` filter, to keep request URLs short."""

ApiCall = Callable[..., object]
"""`mwclient.Site.api`: `api("cargoquery", tables=..., ...)` returns the decoded JSON."""


class LeaguepediaError(RuntimeError):
    """Leaguepedia could not be queried (rate limit, unexpected payload)."""


class _Model(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True)


class _CargoRow(_Model):
    title: dict[str, str | None]


class _CargoLimits(_Model):
    cargoquery: int


class _CargoResponse(_Model):
    cargoquery: list[_CargoRow]
    limits: _CargoLimits | None = None
    """The maximum number of rows the server returns per request, for this account."""


class LeaguepediaPlayer(_Model):
    """A row of the `Players` table: a player or a staff member of a team."""

    page: str = Field(alias="Page")
    """Unique page name, which `RosterChanges` uses: "Castle (Cho Hyeon-seong)"."""
    id: str = Field(alias="ID")
    """In-game name, as shown on the wiki."""
    name: str = Field(alias="Name")
    team: str = Field(alias="Team")
    role: str = Field(alias="Role")
    """Top, Jungle, Mid, Bot, Support for players; Coach, Analyst, Manager... for staff."""
    country: str | None = Field(alias="Country")

    @field_validator("country", mode="before")
    @classmethod
    def _empty_as_none(cls, value: object) -> object:
        return value or None


class LeaguepediaTeam(_Model):
    """A row of the `Teams` table."""

    page: str = Field(alias="Page")
    """Unique page name, which other tables use to refer to the team. It can differ from
    the displayed name: "LYON (2024 American Team)" is displayed "LYON"."""
    name: str = Field(alias="Name")
    short: str = Field(alias="Short")
    is_disbanded: bool = Field(alias="IsDisbanded")

    @field_validator("short", mode="before")
    @classmethod
    def _empty_short(cls, value: object) -> object:
        return value or ""

    @field_validator("is_disbanded", mode="before")
    @classmethod
    def _cargo_boolean(cls, value: object) -> bool:
        return value == "1"


class LeaguepediaTournamentPlayer(_Model):
    """A player or staff member registered by a team for a tournament."""

    team: str = Field(alias="Team")
    player: str = Field(alias="Player")
    """Page name of the player: "Maru (Lee Sang-hun)"."""
    name: str = Field(alias="DisplayName")
    """In-game name from the player's page ("Maru"), empty if the page is missing."""
    real_name: str = Field(alias="RealName")
    role: str = Field(alias="Role")
    """Top, Jungle, Mid, Bot, Support, Coach...; a player who switched lanes has "Top,Bot"."""
    country: str | None = Field(alias="Country")
    tournament: str = Field(alias="Tournament")
    start: date | None = Field(alias="DateStart")
    end: date | None = Field(alias="DateEnd")

    @field_validator("country", "start", "end", mode="before")
    @classmethod
    def _empty_as_none(cls, value: object) -> object:
        return value or None

    @field_validator("name", "real_name", mode="before")
    @classmethod
    def _empty_as_blank(cls, value: object) -> object:
        return value or ""

    @property
    def display_name(self) -> str:
        return self.name or self.player


class LeaguepediaRosterJoin(_Model):
    """A player or staff member joining a team, from the `RosterChanges` table.

    The `Players` table has an `IsSubstitute` field, but the wiki never fills it: whether a
    player is a substitute or inactive is only recorded on their roster changes. A change of
    status (a substitute promoted, a starter benched) is recorded as a new join.
    """

    player: str = Field(alias="Player")
    """Page name of the player, as in `LeaguepediaPlayer.page`."""
    team: str = Field(alias="Team")
    joined: date | None = Field(alias="Date")
    role_modifier: str = Field(alias="RoleModifier")
    """"Sub" for a substitute, empty otherwise."""
    status: str = Field(alias="Status")
    """Empty, or "inactive", "temp_sub", "official_sub", "loaned_out", "on_loan", "trial"..."""

    @field_validator("joined", mode="before")
    @classmethod
    def _date_part(cls, value: object) -> object:
        # Cargo datetimes look like "2026-09-08 00:00:00".
        return value[:10] if isinstance(value, str) and value else None

    @field_validator("role_modifier", "status", mode="before")
    @classmethod
    def _empty_as_blank(cls, value: object) -> object:
        return value or ""


class LeaguepediaClient:
    """Paced, paginated and validated access to Cargo tables."""

    def __init__(
        self,
        api: ApiCall,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api = api
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None

    def query(
        self,
        tables: str,
        fields: Iterable[str],
        where: str | None = None,
        order_by: str | None = None,
        join_on: str | None = None,
    ) -> list[dict[str, str | None]]:
        """Return every row matching the query, one paced request per page."""
        params: dict[str, str | int] = {"tables": tables, "fields": ",".join(fields)}
        if join_on:
            params["join_on"] = join_on
        if where:
            params["where"] = where
        if order_by:
            params["order_by"] = order_by
        rows: list[dict[str, str | None]] = []
        while True:
            page = self._request({**params, "limit": PAGE_SIZE, "offset": len(rows)})
            rows.extend(_decoded(row.title) for row in page.cargoquery)
            # The server may cap pages below PAGE_SIZE: a page is the last one only if it
            # is shorter than what the server allows, otherwise results would be truncated.
            page_cap = min(PAGE_SIZE, page.limits.cargoquery) if page.limits else PAGE_SIZE
            if len(page.cargoquery) < page_cap:
                return rows

    def fetch_players(self, teams: Iterable[str]) -> list[LeaguepediaPlayer]:
        """Return the current players and staff of `teams`, as listed on the wiki."""
        names = sorted(set(teams))
        players: list[LeaguepediaPlayer] = []
        for start in range(0, len(names), TEAMS_PER_QUERY):
            chunk = names[start : start + TEAMS_PER_QUERY]
            rows = self.query(
                "Players",
                ["_pageName=Page", "ID", "Name", "Team", "Role", "Country"],
                where=f"Team IN ({', '.join(cargo_quote(name) for name in chunk)})",
                order_by="Team, ID",
            )
            players.extend(_validate(LeaguepediaPlayer, row, "Players") for row in rows)
        return players

    def fetch_teams_by_short(self, codes: Iterable[str]) -> list[LeaguepediaTeam]:
        """Return the teams whose short name is one of `codes`, disbanded ones included."""
        unique = sorted(set(codes))
        teams: list[LeaguepediaTeam] = []
        for start in range(0, len(unique), TEAMS_PER_QUERY):
            chunk = unique[start : start + TEAMS_PER_QUERY]
            rows = self.query(
                "Teams",
                ["_pageName=Page", "Name", "Short", "IsDisbanded"],
                where=f"Short IN ({', '.join(cargo_quote(code) for code in chunk)})",
                order_by="Name",
            )
            teams.extend(_validate(LeaguepediaTeam, row, "Teams") for row in rows)
        return teams

    def fetch_tournament_rosters(
        self, teams: Iterable[str], since: date
    ) -> list[LeaguepediaTournamentPlayer]:
        """Return who `teams` registered for the tournaments started since `since`.

        Used for the last known roster of teams that have no current roster on the wiki
        (between seasons, or after their players left).
        """
        names = sorted(set(teams))
        rows_out: list[LeaguepediaTournamentPlayer] = []
        for start in range(0, len(names), TEAMS_PER_QUERY):
            chunk = names[start : start + TEAMS_PER_QUERY]
            rows = self.query(
                "TournamentPlayers=TP, Tournaments=T, Players=P",
                [
                    "TP.Team=Team",
                    "TP.Link=Player",
                    "P.ID=DisplayName",
                    "P.Name=RealName",
                    "TP.Role=Role",
                    "TP.Flag=Country",
                    "T.Name=Tournament",
                    "T.DateStart=DateStart",
                    "T.Date=DateEnd",
                ],
                # Cargo joins are left joins: a player without a page keeps its row.
                join_on="TP.OverviewPage=T.OverviewPage, TP.Link=P._pageName",
                where=(
                    f"TP.Team IN ({', '.join(cargo_quote(name) for name in chunk)})"
                    f" AND T.DateStart >= {cargo_quote(since.isoformat())}"
                ),
                order_by="T.DateStart DESC, TP.Team, TP.Link",
            )
            rows_out.extend(
                _validate(LeaguepediaTournamentPlayer, row, "TournamentPlayers") for row in rows
            )
        return rows_out

    def fetch_roster_joins(self, teams: Iterable[str]) -> list[LeaguepediaRosterJoin]:
        """Return every join recorded for `teams`, most recent first."""
        names = sorted(set(teams))
        joins: list[LeaguepediaRosterJoin] = []
        for start in range(0, len(names), TEAMS_PER_QUERY):
            chunk = names[start : start + TEAMS_PER_QUERY]
            rows = self.query(
                "RosterChanges",
                ["Date_Sort=Date", "Player", "Team", "RoleModifier", "Status"],
                where=(
                    f"Team IN ({', '.join(cargo_quote(name) for name in chunk)})"
                    ' AND Direction = "Join"'
                ),
                order_by="Date_Sort DESC, Team, Player",
            )
            joins.extend(_validate(LeaguepediaRosterJoin, row, "RosterChanges") for row in rows)
        return joins

    def _request(self, params: Mapping[str, str | int]) -> _CargoResponse:
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            self._wait_for_turn()
            try:
                payload = self._api("cargoquery", **params)
            except Exception as exc:
                # mwclient raises APIError with a `code`; only rate limits are retried. Any
                # other failure (network, refused query) becomes a LeaguepediaError, which
                # callers treat as "Leaguepedia unavailable".
                if getattr(exc, "code", None) != "ratelimited":
                    raise LeaguepediaError("Leaguepedia request failed") from exc
                if attempt == MAX_RATE_LIMIT_RETRIES:
                    raise LeaguepediaError("Still rate limited after several retries") from exc
                logger.warning("Leaguepedia rate limit, waiting %.0f s", RATE_LIMIT_WAIT_SECONDS)
                self._sleep(RATE_LIMIT_WAIT_SECONDS)
                continue
            try:
                return _CargoResponse.model_validate(payload)
            except ValidationError as exc:
                raise LeaguepediaError(f"Unexpected cargoquery payload: {exc}") from exc
        raise AssertionError("unreachable")

    def _wait_for_turn(self) -> None:
        now = self._clock()
        if self._last_request is not None:
            wait = self._last_request + MIN_REQUEST_INTERVAL_SECONDS - now
            if wait > 0:
                self._sleep(wait)
                now += wait
        self._last_request = now


def _decoded(row: Mapping[str, str | None]) -> dict[str, str | None]:
    """Decode the HTML entities Cargo leaves in values: "Ian&nbsp;Victor" -> "Ian Victor"."""
    return {
        key: html.unescape(value).replace("\xa0", " ") if value else value
        for key, value in row.items()
    }


def cargo_quote(value: str) -> str:
    """Quote a value for a Cargo `where` clause, escaping backslashes and double quotes."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def make_client(settings: Settings) -> LeaguepediaClient:
    """Log in to Leaguepedia with the bot password if configured, anonymously otherwise."""
    # Imported here: building a client connects to the wiki, which tests never do.
    from mwcleric.auth_credentials import AuthCredentials
    from mwrogue.esports_client import EsportsClient

    credentials = None
    if settings.leaguepedia_bot_username and settings.leaguepedia_bot_password:
        credentials = AuthCredentials(
            username=settings.leaguepedia_bot_username,
            password=settings.leaguepedia_bot_password.get_secret_value(),
        )
    else:
        logger.warning("No Leaguepedia bot credentials: anonymous limits are much lower")
    site = EsportsClient("lol", credentials=credentials, max_retries=1)
    api: ApiCall = site.client.api
    return LeaguepediaClient(api)


def _validate[T: BaseModel](model: type[T], row: Mapping[str, object], table: str) -> T:
    try:
        return model.model_validate(row)
    except ValidationError as exc:
        raise LeaguepediaError(f"Unexpected {table} row: {exc}") from exc
