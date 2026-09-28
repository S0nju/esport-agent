import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

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


def make_team(team_id: str = "t1", players: tuple[PlayerRecord, ...] = ()) -> TeamRecord:
    return TeamRecord(
        id=team_id,
        slug=f"team-{team_id}",
        name=f"Team {team_id}",
        code=team_id.upper(),
        status="active",
        home_league="LEC",
        players=players,
    )


def make_player(player_id: str, role: str = "mid") -> PlayerRecord:
    return PlayerRecord(
        id=player_id, summoner_name=player_id, first_name="First", last_name="Last", role=role
    )


def make_match(match_id: str = "m1", state: str = "unstarted") -> MatchRecord:
    return MatchRecord(
        id=match_id,
        start_time=datetime(2026, 9, 19, 15, tzinfo=UTC),
        state=state,
        league_slug="lec",
        league_name="LEC",
        block_name="Playoffs",
        best_of=5,
        team1=MatchSide(name="Team A", code="A", outcome=None, game_wins=None),
        team2=MatchSide(name="Team B", code="B", outcome=None, game_wins=None),
    )


def test_connect_uses_row_factory_and_foreign_keys(tmp_path: Path) -> None:
    conn = connect(tmp_path / "test.db")
    try:
        assert conn.row_factory is sqlite3.Row
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_init_schema_is_idempotent(conn: sqlite3.Connection) -> None:
    init_schema(conn)
    init_schema(conn)

    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master")}
    assert {"teams", "players", "matches"} <= tables


def test_replace_teams_inserts_teams_and_players(conn: sqlite3.Connection) -> None:
    replace_teams(conn, [make_team("t1", (make_player("p1"), make_player("p2", "top")))])

    team = conn.execute("SELECT * FROM teams").fetchone()
    assert (team["id"], team["name"], team["home_league"]) == ("t1", "Team t1", "LEC")
    roles = {row["id"]: row["role"] for row in conn.execute("SELECT * FROM players")}
    assert roles == {"p1": "mid", "p2": "top"}


def test_replace_teams_drops_previous_snapshot(conn: sqlite3.Connection) -> None:
    replace_teams(conn, [make_team("t1", (make_player("p1"),))])
    replace_teams(conn, [make_team("t2", (make_player("p2"),))])

    assert [row["id"] for row in conn.execute("SELECT id FROM teams")] == ["t2"]
    assert [row["id"] for row in conn.execute("SELECT id FROM players")] == ["p2"]


def test_players_require_an_existing_team(conn: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO players (team_id, id, summoner_name, first_name, last_name, role)"
            " VALUES ('missing', 'p1', 'p1', 'f', 'l', 'mid')"
        )


def test_upsert_matches_inserts_then_updates(conn: sqlite3.Connection) -> None:
    upsert_matches(conn, [make_match("m1")])
    finished = replace(
        make_match("m1", state="completed"),
        team1=MatchSide(name="Team A", code="A", outcome="loss", game_wins=0),
        team2=MatchSide(name="Team B", code="B", outcome="win", game_wins=3),
    )
    upsert_matches(conn, [finished, make_match("m2")])

    rows = {row["id"]: row for row in conn.execute("SELECT * FROM matches")}
    assert set(rows) == {"m1", "m2"}
    assert rows["m1"]["state"] == "completed"
    assert (rows["m1"]["team1_game_wins"], rows["m1"]["team2_game_wins"]) == (0, 3)
    assert rows["m1"]["team2_outcome"] == "win"
    assert rows["m2"]["team1_outcome"] is None


def test_upsert_matches_stores_start_time_in_utc(conn: sqlite3.Connection) -> None:
    paris = timezone(timedelta(hours=2))
    match = replace(make_match(), start_time=datetime(2026, 9, 19, 17, tzinfo=paris))

    upsert_matches(conn, [match])

    stored = conn.execute("SELECT start_time FROM matches").fetchone()[0]
    assert stored == "2026-09-19T15:00:00+00:00"
