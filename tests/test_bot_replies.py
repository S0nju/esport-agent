import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from esport_agent.bot.replies import (
    error_message,
    format_next_match,
    format_results,
    format_roster,
    next_match_reply,
    results_reply,
    roster_reply,
)
from esport_agent.config import Settings
from esport_agent.db import StaffRecord, replace_teams, upsert_matches
from esport_agent.tools.handlers import (
    MatchInfo,
    NextMatchResponse,
    PlayerInfo,
    RecentResultsResponse,
    ResultInfo,
    RosterResponse,
    TeamInfo,
)
from tests.factories import make_match, make_player, make_team

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
TEAM: TeamInfo = {"name": "Example Esports", "code": "EX", "league": "LEC"}
LANES = ("Top", "Jungle", "Mid", "Bot", "Support")


def player(name: str, role: str, substitute: bool = False) -> PlayerInfo:
    return {
        "summoner_name": name,
        "real_name": "",
        "role": role,
        "country": None,
        "substitute": substitute,
    }


def roster(*players: PlayerInfo) -> RosterResponse:
    return {"team": TEAM, "active": True, "players": list(players), "staff": []}


def full_roster() -> RosterResponse:
    return roster(*(player(f"P{role}", role) for role in LANES))


def match(state: str = "unstarted", **changes: object) -> MatchInfo:
    info: MatchInfo = {
        "start_time": "2026-10-04T18:00:00+02:00",
        "weekday": "Saturday",
        "state": state,
        "league": "LEC",
        "stage": "Week 1",
        "best_of": 3,
        "opponent": "Other Team",
    }
    return {**info, **changes}  # type: ignore[typeddict-item]


def result(score: str, outcome: str | None, start: str) -> ResultInfo:
    return {**match("completed", start_time=start), "score": score, "result": outcome}


def test_roster_in_french_and_english() -> None:
    team = full_roster()
    team["players"].append(player("Bench_Guy", "Mid", substitute=True))

    assert format_roster(team, "fr") == (
        "Roster de **Example Esports** :\n"
        "- Top : PTop\n- Jungle : PJungle\n- Mid : PMid\n- Bot : PBot\n- Support : PSupport\n"
        "- Mid : Bench\\_Guy (remplaçant)"
    )
    assert format_roster(team, "en").splitlines()[0] == "Roster of **Example Esports**:"
    assert format_roster(team, "en").splitlines()[-1] == "- Mid: Bench\\_Guy (sub)"


def test_roster_notes_are_rebuilt_in_the_user_language() -> None:
    team = roster(player("A", "Top"), player("B", "Top"), player("C", "Mid"))

    lines = format_roster(team, "fr").splitlines()

    assert lines[-2] == (
        "-# Plusieurs titulaires pour un même rôle : le roster peut inclure des remplaçants "
        "ou des joueurs inactifs."
    )
    assert (
        lines[-1]
        == "-# Aucun titulaire en Jungle, Bot, Support : le roster est peut-être incomplet."
    )


def test_last_known_roster_says_it_is_no_longer_active() -> None:
    team = full_roster()
    team["active"] = False
    team["last_known_roster"] = {"tournament": "EX League Summer", "ended": "2026-09-02"}
    noon = int(datetime(2026, 9, 2, 12, tzinfo=UTC).timestamp())

    title = format_roster(team, "en").splitlines()[0]

    assert title == (
        f"Last known roster of **Example Esports** (EX League Summer, ended <t:{noon}:D>), "
        "no longer active:"
    )


def test_staff_only_on_request() -> None:
    team = full_roster()
    team["staff"] = [{"name": "Coachy", "real_name": "", "role": "Coach", "country": None}]

    assert "Coachy" not in format_roster(team, "fr")
    assert format_roster(team, "fr", staff=True).splitlines()[-2:] == [
        "Staff :",
        "- Coach : Coachy",
    ]


def test_empty_roster() -> None:
    assert format_roster(roster(), "fr") == "Aucun joueur connu pour **Example Esports**."


def test_next_match_uses_discord_timestamps() -> None:
    reply: NextMatchResponse = {"team": TEAM, "next_match": match()}
    start = int(datetime(2026, 10, 4, 16, tzinfo=UTC).timestamp())

    assert format_next_match(reply, "fr") == (
        f"Prochain match de **Example Esports** : vs Other Team, <t:{start}:F> (<t:{start}:R>)"
        " · LEC, Week 1, Bo3"
    )


def test_live_and_missing_next_match() -> None:
    live: NextMatchResponse = {"team": TEAM, "next_match": match("inProgress", stage=None)}
    none: NextMatchResponse = {"team": TEAM, "next_match": None}

    assert format_next_match(live, "en") == (
        "**Example Esports** is playing Other Team right now · LEC, Bo3"
    )
    assert format_next_match(none, "fr") == "Aucun match à venir pour **Example Esports**."


def test_results() -> None:
    reply: RecentResultsResponse = {
        "team": TEAM,
        "results": [
            result("3-1", "win", "2026-09-19T17:00:00+02:00"),
            result("0-2", "loss", "2026-09-12T17:00:00+02:00"),
        ],
    }
    first = int(datetime(2026, 9, 19, 15, tzinfo=UTC).timestamp())

    lines = format_results(reply, "en").splitlines()

    assert lines[0] == "Last results of **Example Esports**:"
    assert lines[1] == f"- <t:{first}:d>: ✅ 3-1 vs Other Team (LEC, Week 1)"
    assert lines[2].endswith("❌ 0-2 vs Other Team (LEC, Week 1)")
    assert format_results({"team": TEAM, "results": []}, "fr") == (
        "Aucun résultat pour **Example Esports**."
    )


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("unknown_team", "Aucune équipe ne correspond à « Team\\_X »."),
        ("unknown_league", "Aucune ligue suivie ne correspond à « LCK »."),
        ("not_in_league", "Aucune équipe « Team\\_X » ne joue en LCK."),
    ],
)
def test_errors_are_worded_from_their_code(code: str, expected: str) -> None:
    error = {"error": "English text for Claude", "code": code, "candidates": []}

    assert error_message(error, "fr", "Team_X", "LCK") == expected  # type: ignore[arg-type]


def test_ambiguous_team_lists_a_limited_number_of_candidates() -> None:
    candidates = [f"Team {i} (T{i}, LEC)" for i in range(12)]
    error = {"error": "...", "code": "ambiguous_team", "candidates": candidates}

    lines = error_message(error, "en", "Team", None).splitlines()  # type: ignore[arg-type]

    assert lines[0] == 'Several teams match "Team", which one do you mean?'
    assert lines[1] == "- Team 0 (T0, LEC)"
    assert len(lines) == 1 + 10 + 1
    assert lines[-1] == "- …"


@pytest.fixture
def db(conn: sqlite3.Connection) -> sqlite3.Connection:
    lanes = ("top", "jungle", "mid", "bottom", "support")
    kc = replace(
        make_team(
            "kc", tuple(make_player(f"P{i}", r) for i, r in enumerate(lanes)), name="Karmine Corp"
        ),
        code="KC",
        staff=(StaffRecord(name="Coachy", real_name="", role="Coach"),),
    )
    replace_teams(conn, [kc, make_team("g2", name="G2 Esports", code="G2")])
    upsert_matches(
        conn,
        [
            make_match("past", team1="Karmine Corp", team2="G2 Esports", score=(3, 1)),
            make_match(
                "next",
                team1="G2 Esports",
                team2="Karmine Corp",
                start_time=NOW + timedelta(days=2),
            ),
        ],
    )
    return conn


def test_replies_use_the_default_team(db: sqlite3.Connection, settings: Settings) -> None:
    assert roster_reply(db, settings, "en", None, staff=True).startswith(
        "Roster of **Karmine Corp**:"
    )
    assert "vs G2 Esports" in next_match_reply(db, settings, "en", None, now=NOW)
    assert "✅ 3-1 vs G2 Esports" in results_reply(db, settings, "fr", "KC", limit=3)


def test_replies_word_tool_errors(db: sqlite3.Connection, settings: Settings) -> None:
    assert roster_reply(db, settings, "fr", "Nobody") == "Aucune équipe ne correspond à « Nobody »."
    assert results_reply(db, settings, "en", "KC", league="LCK") == (
        'No tracked league matches "LCK".'
    )


def test_last_known_roster_without_end_date() -> None:
    team = full_roster()
    team["last_known_roster"] = {"tournament": "Cup", "ended": None}

    assert format_roster(team, "fr").splitlines()[0] == (
        "Dernier roster connu de **Example Esports** (Cup), plus actif :"
    )


def test_markdown_is_escaped_in_names() -> None:
    team = roster(player("*Star*", "Top"), player("[link](http://x)", "Mid"))

    lines = format_roster(team, "en").splitlines()

    assert lines[1] == "- Top: \\*Star\\*"
    assert lines[2] == "- Mid: \\[link\\](http://x)"
