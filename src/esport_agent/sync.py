"""Update the local SQLite database from the external sources.

lolesports gives all teams with their rosters, and the schedule of the configured leagues.
Leaguepedia, when bot credentials are configured, then replaces the rosters of the teams
playing in those leagues with its own (starters, substitutes, staff, countries).

Usage: `uv run python -m esport_agent.sync`
"""

import logging
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import date

import httpx

from esport_agent.config import MissingSettingError, Settings, get_settings, require_secret
from esport_agent.data import leaguepedia
from esport_agent.data.leaguepedia import LeaguepediaError
from esport_agent.data.lolesports import Event, LolesportsClient, LolesportsFormatError, Team
from esport_agent.db import (
    MatchRecord,
    MatchSide,
    PlayerRecord,
    TeamRecord,
    connect,
    delete_stale_matches,
    init_schema,
    replace_teams,
    upsert_matches,
)
from esport_agent.logging_config import setup_logging
from esport_agent.rosters import RosterSource, enrich_teams

# Named explicitly: run with `python -m`, __name__ would be "__main__".
logger = logging.getLogger("esport_agent.sync")

MAX_NEWER_PAGES = 5
"""Schedule pages fetched after the current one, to reach upcoming matches."""

HTTP_TIMEOUT_SECONDS = 60.0


@dataclass(frozen=True)
class SyncStats:
    teams: int
    matches: int
    deleted_matches: int = 0


def to_team_record(team: Team) -> TeamRecord:
    """Convert a team, keeping only the first occurrence of each player."""
    players: dict[str, PlayerRecord] = {}
    for p in team.players:
        if p.id in players:
            logger.warning("Duplicate player %s in team %s, skipped", p.id, team.slug)
            continue
        players[p.id] = PlayerRecord(
            id=p.id,
            summoner_name=p.summoner_name,
            first_name=p.first_name,
            last_name=p.last_name,
            role=p.role,
        )
    return TeamRecord(
        id=team.id,
        slug=team.slug,
        name=team.name,
        code=team.code,
        status=team.status,
        home_league=team.home_league.name if team.home_league else None,
        players=tuple(players.values()),
    )


def to_team_records(teams: list[Team]) -> list[TeamRecord]:
    """Convert teams, keeping only the first occurrence of each team id."""
    records: dict[str, TeamRecord] = {}
    for team in teams:
        if team.id in records:
            logger.warning("Duplicate team %s (%s), skipped", team.id, team.slug)
            continue
        records[team.id] = to_team_record(team)
    return list(records.values())


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
    conn: sqlite3.Connection,
    client: LolesportsClient,
    league_slugs: Sequence[str],
    rosters: RosterSource | None = None,
    aliases: Mapping[str, str] | None = None,
) -> SyncStats:
    """Fetch teams and the schedule of `league_slugs`, then write them in one transaction.

    Everything is fetched before anything is written, so a failing API call leaves the
    database untouched. Unfinished matches that a league's schedule no longer returns
    (cancelled, moved to another league) are deleted. With `rosters` (Leaguepedia), the
    rosters of the teams playing in these leagues are enriched; if that fails, the
    lolesports rosters are kept.
    """
    league_ids = {league.slug: league.id for league in client.get_leagues()}
    unknown = [slug for slug in league_slugs if slug not in league_ids]
    if unknown:
        logger.warning("Unknown lolesports leagues, skipped: %s", ", ".join(unknown))

    teams = to_team_records(client.get_teams())
    schedules: dict[str, list[MatchRecord]] = {}
    for slug in league_slugs:
        if slug in league_ids:
            schedules[slug] = fetch_league_matches(client, league_ids[slug])
            logger.info("Fetched schedule of %s", slug)
    matches = {match.id: match for records in schedules.values() for match in records}
    if rosters is not None:
        teams = enrich_rosters(teams, matches.values(), rosters, aliases)

    deleted = 0
    with conn:
        replace_teams(conn, teams)
        upsert_matches(conn, matches.values())
        for records in schedules.values():
            # Group by the slug stored on the matches, which is what the query filters on.
            for league_slug in {m.league_slug for m in records}:
                league_matches = [m for m in records if m.league_slug == league_slug]
                deleted += delete_stale_matches(
                    conn,
                    league_slug,
                    start=min(m.start_time for m in league_matches),
                    end=max(m.start_time for m in league_matches),
                    keep_ids={m.id for m in league_matches},
                )
    if deleted:
        logger.info("Deleted %d cancelled or moved matches", deleted)
    return SyncStats(teams=len(teams), matches=len(matches), deleted_matches=deleted)


def enrich_rosters(
    teams: list[TeamRecord],
    matches: Iterable[MatchRecord],
    rosters: RosterSource,
    aliases: Mapping[str, str] | None = None,
    today: date | None = None,
) -> list[TeamRecord]:
    """Enrich the rosters of the teams playing in `matches`, or keep them on any failure."""
    tracked: dict[str, str] = {}
    for match in matches:
        for side in (match.team1, match.team2):
            if side.name != "TBD":
                tracked[side.name] = side.code
    try:
        enriched, stats = enrich_teams(
            teams, tracked, rosters, today=today or date.today(), aliases=aliases
        )
    except LeaguepediaError as exc:
        # Leaguepedia is a bonus: a failure (rate limit, network, format) must not prevent
        # the lolesports data from being synced. Expected, so one line; details at DEBUG.
        logger.warning(
            "Leaguepedia rosters unavailable (%s), keeping the lolesports rosters", _reason(exc)
        )
        logger.debug("Leaguepedia error details", exc_info=True)
        return teams
    except Exception:
        # A bug in the matching code: worth a full traceback, but the sync still completes.
        logger.exception("Leaguepedia rosters failed, keeping the lolesports rosters")
        return teams
    logger.info(
        "Leaguepedia rosters: %d teams matched by alias, %d by name, %d by code (%d of them"
        " with a last known roster only), %d unmatched: %s; %d substitutes flagged, %d"
        " inactive or loaned out players left out",
        stats.by_alias,
        stats.by_name,
        stats.by_code,
        stats.last_known,
        len(stats.unmatched),
        ", ".join(stats.unmatched) or "none",
        stats.substitutes,
        stats.away,
    )
    return enriched


def make_roster_source(settings: Settings) -> RosterSource | None:
    """Log in to Leaguepedia if bot credentials are configured, otherwise skip it."""
    if not (settings.leaguepedia_bot_username and settings.leaguepedia_bot_password):
        logger.info("No Leaguepedia credentials: rosters come from lolesports only")
        return None
    try:
        return leaguepedia.make_client(settings)
    except Exception as exc:
        # Same as above: a failed login (network, wrong password) only means rosters stay as
        # lolesports gives them.
        logger.warning(
            "Leaguepedia login failed (%s), rosters come from lolesports only", _reason(exc)
        )
        logger.debug("Leaguepedia login error details", exc_info=True)
        return None


def _reason(exc: BaseException) -> str:
    """Return the root cause of an error on one line.

    "gaierror: [Errno -2] Name or service not known" says more than the chain of wrappers
    that a network failure goes through.
    """
    root, seen = exc, {id(exc)}
    while (cause := root.__cause__ or root.__context__) is not None and id(cause) not in seen:
        root = cause
        seen.add(id(cause))
    lines = str(root).splitlines()
    return f"{type(root).__name__}: {lines[0]}" if lines else type(root).__name__


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level or "INFO", settings.log_file)
    try:
        api_key = require_secret(settings.lolesports_api_key, "LOLESPORTS_API_KEY")
    except MissingSettingError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        with (
            closing(connect(settings.sqlite_path)) as conn,
            httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as http,
        ):
            init_schema(conn)
            client = LolesportsClient(http, api_key)
            rosters = make_roster_source(settings)
            stats = run_sync(
                conn,
                client,
                settings.lolesports_leagues,
                rosters,
                settings.leaguepedia_team_aliases,
            )
    except (httpx.HTTPError, LolesportsFormatError) as exc:
        # Expected when the network or the API is down: a clear message and a failing exit
        # code for the scheduler, without a traceback (shown at DEBUG). Nothing was written.
        logger.error(
            "lolesports unavailable (%s): sync aborted, the database is unchanged", _reason(exc)
        )
        logger.debug("lolesports error details", exc_info=True)
        raise SystemExit(1) from exc
    logger.info(
        "Synced %d teams and %d matches into %s", stats.teams, stats.matches, settings.sqlite_path
    )


if __name__ == "__main__":
    main()
