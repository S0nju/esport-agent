import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from esport_agent.db import (
    MatchSide,
    StaffRecord,
    connect,
    delete_stale_matches,
    find_leagues,
    find_teams,
    init_schema,
    latest_league,
    next_match,
    recent_results,
    replace_teams,
    upsert_matches,
)
from tests.factories import make_match, make_player, make_team


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


def test_delete_stale_matches_only_removes_unfinished_matches_in_window(
    conn: sqlite3.Connection,
) -> None:
    day = timedelta(days=1)
    base = make_match()
    upsert_matches(
        conn,
        [
            replace(base, id="kept"),
            replace(base, id="cancelled", start_time=base.start_time + day),
            replace(base, id="completed", state="completed", start_time=base.start_time + day),
            replace(base, id="before_window", start_time=base.start_time - 10 * day),
            replace(base, id="after_window", start_time=base.start_time + 10 * day),
            replace(base, id="other_league", league_slug="lfl", start_time=base.start_time + day),
        ],
    )

    deleted = delete_stale_matches(
        conn, "lec", start=base.start_time, end=base.start_time + 2 * day, keep_ids={"kept"}
    )

    assert deleted == 1
    remaining = {row["id"] for row in conn.execute("SELECT id FROM matches")}
    assert remaining == {"kept", "completed", "before_window", "after_window", "other_league"}


def test_find_teams_exact_match_on_name_code_or_slug(conn: sqlite3.Connection) -> None:
    replace_teams(conn, [make_team("kc", name="Karmine Corp", code="KC")])

    for query in ("karmine corp", "KC", "kc", "team-kc", "  KC  "):
        teams, exact = find_teams(conn, query)
        assert exact, query
        assert [t.name for t in teams] == ["Karmine Corp"], query


def test_find_teams_ranks_homonyms(conn: sqlite3.Connection) -> None:
    replace_teams(
        conn,
        [
            make_team("old", name="Alliance", status="archived"),
            make_team("amateur", name="Alliance", home_league=None),
            make_team("pro", (make_player("p1"),), name="Alliance"),
        ],
    )

    teams, exact = find_teams(conn, "Alliance")

    assert exact
    assert [t.id for t in teams] == ["pro", "amateur", "old"]
    assert [p.id for p in teams[0].players] == ["p1"]


def test_find_teams_falls_back_to_partial_names(conn: sqlite3.Connection) -> None:
    replace_teams(
        conn,
        [
            make_team("kc", name="Karmine Corp", code="KC"),
            make_team("kcb", name="Karmine Corp Blue", code="KCB", home_league="LFL"),
            make_team("g2", name="G2 Esports", code="G2"),
        ],
    )

    teams, exact = find_teams(conn, "karmine")

    assert not exact
    assert {t.name for t in teams} == {"Karmine Corp", "Karmine Corp Blue"}


def test_find_teams_escapes_like_wildcards(conn: sqlite3.Connection) -> None:
    replace_teams(conn, [make_team("a", name="Team A"), make_team("b", name="100% Team")])

    teams, _ = find_teams(conn, "%")

    assert [t.name for t in teams] == ["100% Team"]


def test_next_match_returns_the_first_unfinished_match_since(conn: sqlite3.Connection) -> None:
    day = timedelta(days=1)
    now = datetime(2026, 10, 1, 12, tzinfo=UTC)
    upsert_matches(
        conn,
        [
            make_match("past", start_time=now - day),
            make_match("played", start_time=now + day, score=(2, 0)),
            make_match("later", start_time=now + 3 * day),
            make_match("next", start_time=now + 2 * day, team1="Team B", team2="Team A"),
            make_match("other", start_time=now + day, team1="Team C", team2="Team D"),
        ],
    )

    match = next_match(conn, "team a", since=now)

    assert match is not None
    assert match.id == "next"
    assert match.start_time == now + 2 * day
    assert next_match(conn, "Team A", since=now + 10 * day) is None


def test_recent_results_most_recent_first(conn: sqlite3.Connection) -> None:
    day = timedelta(days=1)
    base = datetime(2026, 9, 1, tzinfo=UTC)
    upsert_matches(
        conn,
        [make_match(f"m{i}", start_time=base + i * day, score=(2, 1)) for i in range(4)]
        + [make_match("upcoming", start_time=base + 10 * day)],
    )

    results = recent_results(conn, "Team A", limit=3)

    assert [m.id for m in results] == ["m3", "m2", "m1"]
    assert results[0].team1 == MatchSide(name="Team A", code="TEA", outcome="win", game_wins=2)


def test_latest_league_uses_the_most_recent_match(conn: sqlite3.Connection) -> None:
    lfl = replace(make_match("new"), league_name="LFL", start_time=datetime(2026, 9, 1, tzinfo=UTC))
    upsert_matches(conn, [make_match("old", start_time=datetime(2026, 1, 1, tzinfo=UTC)), lfl])

    assert latest_league(conn, "team b") == "LFL"
    assert latest_league(conn, "Unknown") is None


def test_find_leagues_by_slug_or_name_part(conn: sqlite3.Connection) -> None:
    upsert_matches(
        conn,
        [
            replace(make_match("a"), league_slug="lfl", league_name="La Ligue Française"),
            replace(make_match("b"), league_slug="lec", league_name="LEC"),
        ],
    )

    assert find_leagues(conn, "LFL") == {"lfl"}
    assert find_leagues(conn, "ligue") == {"lfl"}
    assert find_leagues(conn, " lec ") == {"lec"}
    assert find_leagues(conn, "Worlds") == set()


def test_match_queries_filter_by_league(conn: sqlite3.Connection) -> None:
    now = datetime(2026, 10, 1, tzinfo=UTC)
    upsert_matches(
        conn,
        [
            replace(make_match("lec", start_time=now - timedelta(days=1), score=(2, 0))),
            replace(
                make_match("worlds", start_time=now - timedelta(days=2), score=(2, 0)),
                league_slug="worlds",
            ),
            replace(make_match("lec-next", start_time=now + timedelta(days=1))),
            replace(
                make_match("worlds-next", start_time=now + timedelta(days=2)),
                league_slug="worlds",
            ),
        ],
    )

    assert [m.id for m in recent_results(conn, "Team A", 5, {"worlds"})] == ["worlds"]
    assert [m.id for m in recent_results(conn, "Team A", 5)] == ["lec", "worlds"]
    worlds_next = next_match(conn, "Team A", now, {"worlds"})
    assert worlds_next is not None and worlds_next.id == "worlds-next"


def test_teams_keep_staff_countries_and_substitutes(conn: sqlite3.Connection) -> None:
    team = replace(
        make_team("kc", name="Karmine Corp", code="KC"),
        players=(
            replace(make_player("Caliste", "bottom"), country="France"),
            replace(make_player("Sub", "mid"), is_substitute=True),
        ),
        staff=(StaffRecord(name="Reapered", real_name="Bok Han-gyu", role="Coach"),),
        leaguepedia_name="Karmine Corp",
    )

    replace_teams(conn, [team])
    replace_teams(conn, [team])  # Staff is replaced too, not duplicated.
    found, _ = find_teams(conn, "KC")

    assert found[0].leaguepedia_name == "Karmine Corp"
    assert {(p.summoner_name, p.country, p.is_substitute) for p in found[0].players} == {
        ("Caliste", "France", False),
        ("Sub", None, True),
    }
    assert found[0].staff == (StaffRecord("Reapered", "Bok Han-gyu", "Coach", None),)
