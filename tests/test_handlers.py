import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from esport_agent.config import Settings
from esport_agent.db import StaffRecord, replace_teams, upsert_matches
from esport_agent.tools import handlers
from esport_agent.tools.handlers import (
    ToolContext,
    execute_tool,
    get_team_next_match,
    get_team_recent_results,
    get_team_roster,
)
from tests.factories import make_match, make_player, make_team

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
DAY = timedelta(days=1)


@pytest.fixture
def db(conn: sqlite3.Connection) -> sqlite3.Connection:
    roster = (
        make_player("Support", "support"),
        make_player("Top", "top"),
        make_player("Mid", "mid"),
        make_player("Bench", "none"),
        make_player("Bot", "bottom"),
        make_player("Jungle", "jungle"),
    )
    replace_teams(
        conn,
        [
            make_team("kc", roster, name="Karmine Corp", code="KC"),
            make_team("kcb", name="Karmine Corp Blue", code="KCB", home_league="LFL"),
            make_team("g2", name="G2 Esports", code="G2"),
            make_team("old", name="Karmine Legends", status="archived"),
        ],
    )
    return conn


def test_roster_is_sorted_by_role(db: sqlite3.Connection) -> None:
    result = get_team_roster(db, "KC")

    assert "error" not in result
    assert result["team"] == {"name": "Karmine Corp", "code": "KC", "league": "LEC"}
    assert [p["role"] for p in result["players"]] == ["Top", "Jungle", "Mid", "Bot", "Support"]
    assert "Bench" not in [p["summoner_name"] for p in result["players"]]
    assert "note" not in result


def test_unknown_team(db: sqlite3.Connection) -> None:
    result = get_team_roster(db, "Fnatic")

    assert result == {"error": "No team matches 'Fnatic'.", "candidates": []}


def test_ambiguous_partial_name_lists_relevant_candidates(db: sqlite3.Connection) -> None:
    result = get_team_roster(db, "Karmine")

    assert result == {
        "error": "Several teams match 'Karmine': ask the user which one, or pick one.",
        "candidates": ["Karmine Corp (KC, LEC)", "Karmine Corp Blue (KCB, LFL)"],
    }


def test_partial_name_with_a_single_relevant_team_is_accepted(db: sqlite3.Connection) -> None:
    result = get_team_roster(db, "G2")

    assert "error" not in result
    assert result["team"]["name"] == "G2 Esports"
    by_partial_name = get_team_roster(db, "esports")
    assert "error" not in by_partial_name
    assert by_partial_name["team"]["name"] == "G2 Esports"


def test_next_match_from_the_team_point_of_view(db: sqlite3.Connection) -> None:
    upsert_matches(
        db,
        [
            make_match("done", start_time=NOW - DAY, team1="Karmine Corp", score=(2, 0)),
            make_match("next", start_time=NOW + DAY, team1="G2 Esports", team2="Karmine Corp"),
        ],
    )

    result = get_team_next_match(db, "Karmine Corp", NOW, PARIS)

    assert "error" not in result
    assert result["next_match"] == {
        "start_time": "2026-10-02T14:00:00+02:00",
        "weekday": "Friday",
        "state": "unstarted",
        "league": "LEC",
        "stage": "Playoffs",
        "best_of": 5,
        "opponent": "G2 Esports",
    }


def test_next_match_includes_a_match_that_just_started(db: sqlite3.Connection) -> None:
    started = NOW - timedelta(hours=1)
    upsert_matches(db, [make_match("live", "inProgress", start_time=started, team1="Karmine Corp")])

    result = get_team_next_match(db, "KC", NOW, PARIS)

    assert "error" not in result
    assert result["next_match"] is not None
    assert result["next_match"]["state"] == "inProgress"


def test_no_next_match(db: sqlite3.Connection) -> None:
    upsert_matches(db, [make_match("stale", start_time=NOW - 2 * DAY, team1="Karmine Corp")])

    result = get_team_next_match(db, "KC", NOW, PARIS)

    assert "error" not in result
    assert result["next_match"] is None


def test_recent_results_scores_from_the_team_point_of_view(db: sqlite3.Connection) -> None:
    upsert_matches(
        db,
        [
            make_match("win", start_time=NOW - 2 * DAY, team1="Karmine Corp", score=(3, 1)),
            make_match(
                "loss",
                start_time=NOW - DAY,
                team1="G2 Esports",
                team2="Karmine Corp",
                score=(3, 1),
            ),
        ],
    )

    result = get_team_recent_results(db, "KC", 5, PARIS)

    assert "error" not in result
    summary = [(r["opponent"], r["score"], r["result"]) for r in result["results"]]
    assert summary == [("G2 Esports", "1-3", "loss"), ("Team B", "3-1", "win")]


@pytest.mark.parametrize(("limit", "expected"), [(0, 1), (-3, 1), (2, 2), (500, 20)])
def test_recent_results_limit_is_clamped(db: sqlite3.Connection, limit: int, expected: int) -> None:
    upsert_matches(
        db,
        [
            make_match(f"m{i}", start_time=NOW - i * DAY, team1="Karmine Corp", score=(2, 0))
            for i in range(1, 26)
        ],
    )

    result = get_team_recent_results(db, "KC", limit, PARIS)

    assert "error" not in result
    assert len(result["results"]) == expected


CTX = ToolContext(default_team="Karmine Corp", now=NOW, tz=PARIS, preferred_leagues=("lec",))


def run(db: sqlite3.Connection, name: str, tool_input: dict[str, object]) -> object:
    output = execute_tool(db, name, tool_input, CTX)
    parsed: object = json.loads(output)
    return parsed


@pytest.mark.parametrize("tool_input", [{}, {"team": ""}, {"team": "  "}, {"team": 42}])
def test_execute_tool_falls_back_to_default_team(
    db: sqlite3.Connection, tool_input: dict[str, object]
) -> None:
    result = run(db, "get_team_roster", tool_input)

    assert isinstance(result, dict)
    assert result["team"]["name"] == "Karmine Corp"


def test_execute_tool_uses_given_team(db: sqlite3.Connection) -> None:
    result = run(db, "get_team_next_match", {"team": "G2"})

    assert result == {
        "team": {"name": "G2 Esports", "code": "G2", "league": "LEC"},
        "next_match": None,
    }


@pytest.mark.parametrize(
    ("tool_input", "expected_limit"),
    [({}, 5), ({"limit": 3}, 3), ({"limit": "3"}, 5), ({"limit": True}, 5)],
)
def test_execute_tool_recent_results_limit(
    db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    tool_input: dict[str, object],
    expected_limit: int,
) -> None:
    calls: list[int] = []

    def fake(
        conn: sqlite3.Connection,
        team: str,
        limit: int,
        tz: ZoneInfo,
        leagues: object,
        league: object,
    ) -> dict[str, str]:
        calls.append(limit)
        return {}

    monkeypatch.setattr(handlers, "get_team_recent_results", fake)

    run(db, "get_team_recent_results", tool_input)

    assert calls == [expected_limit]


def test_execute_tool_keeps_non_ascii_characters(db: sqlite3.Connection) -> None:
    replace_teams(db, [make_team("s", name="Équipe Été", code="EE")])

    output = execute_tool(db, "get_team_roster", {"team": "EE"}, CTX)

    assert "Équipe Été" in output


def test_execute_tool_unknown(db: sqlite3.Connection) -> None:
    with pytest.raises(handlers.UnknownToolError):
        run(db, "does_not_exist", {})


def test_team_league_comes_from_its_latest_match(db: sqlite3.Connection) -> None:
    lfl_match = replace(
        make_match("lfl", start_time=NOW - DAY, team1="Karmine Corp Blue", score=(2, 0)),
        league_name="La Ligue Française",
    )
    upsert_matches(db, [lfl_match])
    replace_teams(db, [make_team("kcb", name="Karmine Corp Blue", code="KCB", home_league="LEC")])

    result = get_team_roster(db, "KCB")

    assert "error" not in result
    assert result["team"]["league"] == "La Ligue Française"


def add_league_matches(db: sqlite3.Connection, league_slug: str, *teams: str) -> None:
    upsert_matches(
        db,
        [
            replace(
                make_match(f"{league_slug}-{team}", start_time=NOW - DAY, team1=team, score=(2, 0)),
                league_slug=league_slug,
            )
            for team in teams
        ],
    )


def test_preferred_league_picks_among_partial_matches(db: sqlite3.Connection) -> None:
    add_league_matches(db, "lec", "Karmine Corp")
    add_league_matches(db, "lfl", "Karmine Corp Blue")

    result = get_team_roster(db, "Karmine", preferred_leagues=("lec",))

    assert "error" not in result
    assert result["team"]["name"] == "Karmine Corp"


def test_preferred_league_uses_any_stored_match_not_only_the_latest(
    db: sqlite3.Connection,
) -> None:
    add_league_matches(db, "lec", "Karmine Corp")
    add_league_matches(db, "lfl", "Karmine Corp Blue")
    # A more recent international match must not hide that the team plays in the LEC.
    upsert_matches(
        db,
        [
            replace(
                make_match("worlds", start_time=NOW, team1="Karmine Corp", score=(1, 0)),
                league_slug="worlds",
            )
        ],
    )

    result = get_team_roster(db, "Karmine", preferred_leagues=("lec",))

    assert "error" not in result
    assert result["team"]["name"] == "Karmine Corp"


def test_preferred_leagues_are_tried_in_order(db: sqlite3.Connection) -> None:
    add_league_matches(db, "lec", "Karmine Corp")
    add_league_matches(db, "lfl", "Karmine Corp Blue")

    result = get_team_roster(db, "Karmine", preferred_leagues=("lfl", "lec"))

    assert "error" not in result
    assert result["team"]["name"] == "Karmine Corp Blue"


def test_several_teams_in_the_preferred_league_stay_ambiguous(db: sqlite3.Connection) -> None:
    add_league_matches(db, "lec", "Karmine Corp", "Karmine Corp Blue")

    result = get_team_roster(db, "Karmine", preferred_leagues=("lec",))

    assert result == {
        "error": "Several teams match 'Karmine': ask the user which one, or pick one.",
        "candidates": ["Karmine Corp (KC, LEC)", "Karmine Corp Blue (KCB, LEC)"],
    }


def test_no_team_in_the_preferred_leagues_stays_ambiguous(db: sqlite3.Connection) -> None:
    add_league_matches(db, "lfl", "Karmine Corp", "Karmine Corp Blue")

    result = get_team_roster(db, "Karmine", preferred_leagues=("lec",))

    assert "error" in result


def test_tool_context_from_settings(settings: Settings) -> None:
    ctx = ToolContext.from_settings(settings, NOW)

    assert ctx.default_team == "Karmine Corp"
    assert ctx.tz.key == "Europe/Paris"
    assert ctx.now == NOW
    assert ctx.now.tzinfo == ctx.tz
    assert ctx.preferred_leagues == ("lec",)


def test_league_selects_the_organization_team_playing_there(db: sqlite3.Connection) -> None:
    add_league_matches(db, "lec", "Karmine Corp")
    add_league_matches(db, "lfl", "Karmine Corp Blue")

    result = get_team_recent_results(db, "KC", 5, PARIS, league="lfl")

    assert "error" not in result
    assert result["team"]["name"] == "Karmine Corp Blue"
    assert [r["league"] for r in result["results"]] == ["LEC"]  # make_match's league name


def test_league_filters_the_matches(db: sqlite3.Connection) -> None:
    upsert_matches(
        db,
        [
            replace(
                make_match("lec", start_time=NOW - DAY, team1="G2 Esports", score=(2, 0)),
                league_slug="lec",
            ),
            replace(
                make_match("worlds", start_time=NOW - 2 * DAY, team1="G2 Esports", score=(1, 0)),
                league_slug="worlds",
                league_name="Worlds",
            ),
            replace(
                make_match("worlds-next", start_time=NOW + 3 * DAY, team1="G2 Esports"),
                league_slug="worlds",
                league_name="Worlds",
            ),
            replace(
                make_match("lec-next", start_time=NOW + DAY, team1="G2 Esports"),
                league_slug="lec",
            ),
        ],
    )

    results = get_team_recent_results(db, "G2", 5, PARIS, league="Worlds")
    upcoming = get_team_next_match(db, "G2", NOW, PARIS, league="worlds")

    assert "error" not in results
    assert [r["league"] for r in results["results"]] == ["Worlds"]
    assert "error" not in upcoming
    assert upcoming["next_match"] is not None
    assert upcoming["next_match"]["league"] == "Worlds"


def test_team_playing_in_the_league_is_kept(db: sqlite3.Connection) -> None:
    add_league_matches(db, "lec", "Karmine Corp")
    add_league_matches(db, "lfl", "Karmine Corp Blue")

    result = get_team_roster(db, "KC", league="lec")

    assert "error" not in result
    assert result["team"]["name"] == "Karmine Corp"


def test_unknown_league(db: sqlite3.Connection) -> None:
    add_league_matches(db, "lec", "Karmine Corp")

    result = get_team_roster(db, "KC", league="Premier League")

    assert result == {"error": "No synced league matches 'Premier League'.", "candidates": []}


def test_no_team_of_the_organization_in_the_league(db: sqlite3.Connection) -> None:
    add_league_matches(db, "lec", "Karmine Corp", "G2 Esports")

    result = get_team_roster(db, "G2", league="lec")
    missing = get_team_roster(db, "KCB", league="lec")

    assert "error" not in result
    assert missing == {"error": "No team matching 'KCB' plays in 'lec'.", "candidates": []}


def test_execute_tool_passes_the_league(db: sqlite3.Connection) -> None:
    add_league_matches(db, "lec", "Karmine Corp")
    add_league_matches(db, "lfl", "Karmine Corp Blue")

    result = run(db, "get_team_roster", {"team": "KC", "league": "LFL"})
    ignored = run(db, "get_team_roster", {"team": "KC", "league": "  "})

    assert isinstance(result, dict) and isinstance(ignored, dict)
    assert result["team"]["name"] == "Karmine Corp Blue"
    assert ignored["team"]["name"] == "Karmine Corp"


def test_roster_notes_repeated_roles(db: sqlite3.Connection) -> None:
    replace_teams(
        db,
        [
            make_team(
                "vit",
                (
                    make_player("FIESTA", "mid"),
                    make_player("Humanoid", "mid"),
                    make_player("Carzzy", "bottom"),
                ),
                name="Team Vitality",
                code="VIT",
            )
        ],
    )

    result = get_team_roster(db, "VIT")

    assert "error" not in result
    assert [(p["role"], p["summoner_name"]) for p in result["players"]] == [
        ("Mid", "FIESTA"),
        ("Mid", "Humanoid"),
        ("Bot", "Carzzy"),
    ]
    assert result.get("note") == handlers.INACTIVE_PLAYERS_NOTE


def test_roster_gives_real_names_countries_substitutes_and_staff(db: sqlite3.Connection) -> None:
    team = replace(
        make_team("kc", name="Karmine Corp", code="KC"),
        players=(
            replace(make_player("Busio", "support"), first_name="Alan Cwalina", last_name=""),
            replace(make_player("Caliste", "bottom"), country="France"),
            replace(make_player("Backup", "bottom"), is_substitute=True),
        ),
        staff=(StaffRecord(name="Reapered", real_name="Bok Han-gyu", role="Coach"),),
    )
    replace_teams(db, [team])

    result = get_team_roster(db, "KC")

    assert "error" not in result
    assert [(p["summoner_name"], p["role"], p["substitute"]) for p in result["players"]] == [
        ("Caliste", "Bot", False),
        ("Busio", "Support", False),
        ("Backup", "Bot", True),
    ]
    assert result["players"][1]["real_name"] == "Alan Cwalina"
    assert result["players"][0]["country"] == "France"
    assert result["staff"] == [
        {"name": "Reapered", "real_name": "Bok Han-gyu", "role": "Coach", "country": None}
    ]
    assert "note" not in result  # A substitute is not a duplicated role.
