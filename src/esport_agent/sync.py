"""Update the local SQLite database from the external sources.

For the MVP, every table is filled from the lolesports API: all teams with their rosters,
and the schedule of the configured leagues.

Usage: `uv run python -m esport_agent.sync`
"""

import logging
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass

import httpx

from esport_agent.config import get_settings
from esport_agent.data.lolesports import Event, LolesportsClient, Team
from esport_agent.db import (
    MatchRecord,
    MatchSide,
    PlayerRecord,
    TeamRecord,
    connect,
    init_schema,
    replace_teams,
    upsert_matches,
)

logger = logging.getLogger(__name__)

MAX_NEWER_PAGES = 5
"""Schedule pages fetched after the current one, to reach upcoming matches."""

HTTP_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class SyncStats:
    teams: int
    matches: int


def to_team_record(team: Team) -> TeamRecord:
    return TeamRecord(
        id=team.id,
        slug=team.slug,
        name=team.name,
        code=team.code,
        status=team.status,
        home_league=team.home_league.name if team.home_league else None,
        players=tuple(
            PlayerRecord(
                id=p.id,
                summoner_name=p.summoner_name,
                first_name=p.first_name,
                last_name=p.last_name,
                role=p.role,
            )
            for p in team.players
        ),
    )


def to_match_record(event: Event) -> MatchRecord | None:
    """Convert a schedule event, or return `None` if it is not a two-team match."""
    if event.match is None or len(event.match.teams) != 2:
        return None
    sides = [
        MatchSide(
            name=team.name,
            code=team.code,
            outcome=team.result.outcome if team.result else None,
            game_wins=team.result.game_wins if team.result else None,
        )
        for team in event.match.teams
    ]
    strategy = event.match.strategy
    return MatchRecord(
        id=event.match.id,
        start_time=event.start_time,
        state=event.state,
        league_slug=event.league.slug,
        league_name=event.league.name,
        block_name=event.block_name,
        best_of=strategy.count if strategy.type == "bestOf" else None,
        team1=sides[0],
        team2=sides[1],
    )


def fetch_league_matches(client: LolesportsClient, league_id: str) -> list[MatchRecord]:
    """Fetch the current schedule page of a league, then the newer ones."""
    schedule = client.get_schedule(league_id)
    events = list(schedule.events)
    newer = schedule.pages.newer
    for _ in range(MAX_NEWER_PAGES):
        if newer is None:
            break
        page = client.get_schedule(league_id, page_token=newer)
        events.extend(page.events)
        newer = page.pages.newer
    return [record for event in events if (record := to_match_record(event)) is not None]


def run_sync(
    conn: sqlite3.Connection, client: LolesportsClient, league_slugs: Sequence[str]
) -> SyncStats:
    """Fetch teams and the schedule of `league_slugs`, then write them in one transaction.

    Everything is fetched before anything is written, so a failing API call leaves the
    database untouched.
    """
    league_ids = {league.slug: league.id for league in client.get_leagues()}
    unknown = [slug for slug in league_slugs if slug not in league_ids]
    if unknown:
        logger.warning("Unknown lolesports leagues, skipped: %s", ", ".join(unknown))

    teams = [to_team_record(team) for team in client.get_teams()]
    matches: dict[str, MatchRecord] = {}
    for slug in league_slugs:
        if slug in league_ids:
            for match in fetch_league_matches(client, league_ids[slug]):
                matches[match.id] = match
            logger.info("Fetched schedule of %s", slug)

    with conn:
        replace_teams(conn, teams)
        upsert_matches(conn, matches.values())
    return SyncStats(teams=len(teams), matches=len(matches))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    with (
        closing(connect(settings.sqlite_path)) as conn,
        httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as http,
    ):
        init_schema(conn)
        client = LolesportsClient(http, settings.lolesports_api_key.get_secret_value())
        stats = run_sync(conn, client, settings.lolesports_leagues)
    logger.info(
        "Synced %d teams and %d matches into %s", stats.teams, stats.matches, settings.sqlite_path
    )


if __name__ == "__main__":
    main()
