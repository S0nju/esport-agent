"""Reads and writes on the local database."""

import sqlite3
from collections.abc import Collection, Iterable
from datetime import UTC, datetime

from esport_agent.db.records import MatchRecord, MatchSide, PlayerRecord, TeamRecord


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
    # Whole seconds keep a fixed-width format, so that text comparisons stay correct.
    return value.astimezone(UTC).isoformat(timespec="seconds")


MAX_TEAM_CANDIDATES = 10

# Most relevant teams first: active, playing in a league, with the largest roster.
_TEAM_RANKING = """
    ORDER BY status = 'active' DESC,
        home_league IS NOT NULL DESC,
        (SELECT count(*) FROM players p WHERE p.team_id = teams.id) DESC,
        name
"""


def find_teams(conn: sqlite3.Connection, query: str) -> tuple[list[TeamRecord], bool]:
    """Search teams by name, code or slug, case-insensitively, most relevant first.

    Return the exact matches if there are any, otherwise the teams whose name contains
    `query`. The boolean tells whether the matches are exact.
    """
    query = query.strip()
    exact = conn.execute(
        "SELECT * FROM teams WHERE name = ? COLLATE NOCASE OR code = ? COLLATE NOCASE"
        " OR slug = ? COLLATE NOCASE" + _TEAM_RANKING + " LIMIT ?",
        (query, query, query, MAX_TEAM_CANDIDATES),
    ).fetchall()
    if exact:
        return [_team_from_row(conn, row) for row in exact], True
    return find_teams_by_name_part(conn, query), False


def find_teams_by_name_part(conn: sqlite3.Connection, text: str) -> list[TeamRecord]:
    """Return the teams whose name contains `text`, case-insensitively, most relevant first."""
    rows = conn.execute(
        "SELECT * FROM teams WHERE name LIKE ? ESCAPE '\\'" + _TEAM_RANKING + " LIMIT ?",
        (f"%{_escape_like(text.strip())}%", MAX_TEAM_CANDIDATES),
    ).fetchall()
    return [_team_from_row(conn, row) for row in rows]


def find_leagues(conn: sqlite3.Connection, text: str) -> set[str]:
    """Return the slugs of the stored leagues whose slug is `text` or whose name contains it.

    "LFL" matches the slug `lfl`, "Ligue" matches the name "La Ligue Française".
    """
    text = text.strip()
    rows = conn.execute(
        """
        SELECT DISTINCT league_slug FROM matches
        WHERE league_slug = :text COLLATE NOCASE OR league_name LIKE :pattern ESCAPE '\\'
        """,
        {"text": text, "pattern": f"%{_escape_like(text)}%"},
    ).fetchall()
    return {str(row["league_slug"]) for row in rows}


def next_match(
    conn: sqlite3.Connection,
    team_name: str,
    since: datetime,
    league_slugs: Collection[str] = (),
) -> MatchRecord | None:
    """Return `team_name`'s first unfinished match scheduled at or after `since`.

    With `league_slugs`, only matches of these leagues are considered.
    """
    league_filter, league_params = _league_filter(league_slugs)
    row = conn.execute(
        f"""
        SELECT * FROM matches
        WHERE (team1_name = ? COLLATE NOCASE OR team2_name = ? COLLATE NOCASE)
            AND state != 'completed'
            AND start_time >= ?
            {league_filter}
        ORDER BY start_time
        LIMIT 1
        """,
        (team_name, team_name, _to_db_time(since), *league_params),
    ).fetchone()
    return _match_from_row(row) if row else None


def recent_results(
    conn: sqlite3.Connection,
    team_name: str,
    limit: int,
    league_slugs: Collection[str] = (),
) -> list[MatchRecord]:
    """Return `team_name`'s last `limit` completed matches, most recent first.

    With `league_slugs`, only matches of these leagues are considered.
    """
    league_filter, league_params = _league_filter(league_slugs)
    rows = conn.execute(
        f"""
        SELECT * FROM matches
        WHERE (team1_name = ? COLLATE NOCASE OR team2_name = ? COLLATE NOCASE)
            AND state = 'completed'
            {league_filter}
        ORDER BY start_time DESC
        LIMIT ?
        """,
        (team_name, team_name, *league_params, limit),
    ).fetchall()
    return [_match_from_row(row) for row in rows]


def _league_filter(league_slugs: Collection[str]) -> tuple[str, tuple[str, ...]]:
    """Return an `AND league_slug IN (...)` clause and its parameters, or nothing."""
    if not league_slugs:
        return "", ()
    # Only "?" placeholders are interpolated in the query, never values.
    placeholders = ", ".join("?" * len(league_slugs))
    return f"AND league_slug IN ({placeholders})", tuple(league_slugs)


def latest_league(conn: sqlite3.Connection, team_name: str) -> str | None:
    """Return the league of `team_name`'s most recent match, if it has any.

    More reliable than the source's home league, which can be the parent organization's
    league (lolesports lists Karmine Corp Blue, an LFL team, in the LEC).
    """
    row = conn.execute(
        """
        SELECT league_name FROM matches
        WHERE team1_name = :team COLLATE NOCASE OR team2_name = :team COLLATE NOCASE
        ORDER BY start_time DESC
        LIMIT 1
        """,
        {"team": team_name},
    ).fetchone()
    return str(row["league_name"]) if row else None


def team_league_slugs(conn: sqlite3.Connection, team_name: str) -> set[str]:
    """Return the slugs of every league in which `team_name` has a stored match."""
    rows = conn.execute(
        """
        SELECT DISTINCT league_slug FROM matches
        WHERE team1_name = :team COLLATE NOCASE OR team2_name = :team COLLATE NOCASE
        """,
        {"team": team_name},
    ).fetchall()
    return {str(row["league_slug"]) for row in rows}


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _team_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> TeamRecord:
    players = conn.execute(
        "SELECT * FROM players WHERE team_id = ? ORDER BY summoner_name COLLATE NOCASE",
        (row["id"],),
    ).fetchall()
    return TeamRecord(
        id=row["id"],
        slug=row["slug"],
        name=row["name"],
        code=row["code"],
        status=row["status"],
        home_league=row["home_league"],
        players=tuple(
            PlayerRecord(
                id=p["id"],
                summoner_name=p["summoner_name"],
                first_name=p["first_name"],
                last_name=p["last_name"],
                role=p["role"],
            )
            for p in players
        ),
    )


def _match_from_row(row: sqlite3.Row) -> MatchRecord:
    return MatchRecord(
        id=row["id"],
        start_time=datetime.fromisoformat(row["start_time"]),
        state=row["state"],
        league_slug=row["league_slug"],
        league_name=row["league_name"],
        block_name=row["block_name"],
        best_of=row["best_of"],
        team1=MatchSide(
            name=row["team1_name"],
            code=row["team1_code"],
            outcome=row["team1_outcome"],
            game_wins=row["team1_game_wins"],
        ),
        team2=MatchSide(
            name=row["team2_name"],
            code=row["team2_code"],
            outcome=row["team2_outcome"],
            game_wins=row["team2_game_wins"],
        ),
    )
