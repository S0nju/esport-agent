"""Reads and writes on the local database."""

import sqlite3
from collections.abc import Collection, Iterable
from datetime import UTC, datetime

from esport_agent.db.records import MatchRecord, TeamRecord


def replace_teams(conn: sqlite3.Connection, teams: Iterable[TeamRecord]) -> None:
    """Replace every team and roster with `teams`.

    Rosters change over time (transfers, retirements), so the previous snapshot is dropped
    rather than merged. The caller owns the transaction.
    """
    conn.execute("DELETE FROM players")
    conn.execute("DELETE FROM teams")
    for team in teams:
        conn.execute(
            "INSERT INTO teams (id, slug, name, code, status, home_league)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (team.id, team.slug, team.name, team.code, team.status, team.home_league),
        )
        conn.executemany(
            "INSERT INTO players (team_id, id, summoner_name, first_name, last_name, role)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (team.id, p.id, p.summoner_name, p.first_name, p.last_name, p.role)
                for p in team.players
            ],
        )


def upsert_matches(conn: sqlite3.Connection, matches: Iterable[MatchRecord]) -> None:
    """Insert new matches and update known ones (state, results, rescheduling).

    Matches that are no longer returned by the source are kept, so the history grows over
    successive syncs. The caller owns the transaction.
    """
    conn.executemany(
        """
        INSERT INTO matches (
            id, start_time, state, league_slug, league_name, block_name, best_of,
            team1_name, team1_code, team1_outcome, team1_game_wins,
            team2_name, team2_code, team2_outcome, team2_game_wins
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            start_time = excluded.start_time,
            state = excluded.state,
            league_slug = excluded.league_slug,
            league_name = excluded.league_name,
            block_name = excluded.block_name,
            best_of = excluded.best_of,
            team1_name = excluded.team1_name,
            team1_code = excluded.team1_code,
            team1_outcome = excluded.team1_outcome,
            team1_game_wins = excluded.team1_game_wins,
            team2_name = excluded.team2_name,
            team2_code = excluded.team2_code,
            team2_outcome = excluded.team2_outcome,
            team2_game_wins = excluded.team2_game_wins
        """,
        [
            (
                m.id,
                _to_db_time(m.start_time),
                m.state,
                m.league_slug,
                m.league_name,
                m.block_name,
                m.best_of,
                m.team1.name,
                m.team1.code,
                m.team1.outcome,
                m.team1.game_wins,
                m.team2.name,
                m.team2.code,
                m.team2.outcome,
                m.team2.game_wins,
            )
            for m in matches
        ],
    )


def delete_stale_matches(
    conn: sqlite3.Connection,
    league_slug: str,
    start: datetime,
    end: datetime,
    keep_ids: Collection[str],
) -> int:
    """Delete unfinished matches of a league that the source no longer returns.

    Only matches scheduled between `start` and `end` (the period the source just returned)
    are considered, so older history is never touched. This removes cancelled or
    rescheduled-away matches that would otherwise stay "upcoming" forever. The caller owns
    the transaction. Return the number of deleted matches.
    """
    # Only "?" placeholders are interpolated in the query, never values.
    placeholders = ", ".join("?" * len(keep_ids))
    cursor = conn.execute(
        f"""
        DELETE FROM matches
        WHERE league_slug = ?
            AND state != 'completed'
            AND start_time BETWEEN ? AND ?
            AND id NOT IN ({placeholders})
        """,
        (
            league_slug,
            _to_db_time(start),
            _to_db_time(end),
            *keep_ids,
        ),
    )
    return cursor.rowcount


def _to_db_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()
