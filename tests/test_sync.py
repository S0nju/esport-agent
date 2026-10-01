import json
import logging
import sqlite3
from collections.abc import Callable, Iterable
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from esport_agent import sync
from esport_agent.config import Settings
from esport_agent.data import leaguepedia
from esport_agent.data.leaguepedia import (
    LeaguepediaError,
    LeaguepediaPlayer,
    LeaguepediaRosterJoin,
    LeaguepediaTeam,
    LeaguepediaTournamentPlayer,
)
from esport_agent.data.lolesports import Event, LolesportsClient, Team
from esport_agent.sync import run_sync, to_match_record, to_team_records

FIXTURES = Path(__file__).parent / "fixtures" / "lolesports"
LEC_ID = "98767991302996019"


# Any: raw JSON payloads loaded from the fixtures, edited by some tests before being served.
def load(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload


def make_client(
    schedule_pages: dict[str | None, dict[str, Any]] | None = None,
    on_request: Callable[[httpx.Request], None] | None = None,
) -> LolesportsClient:
    """Serve the fixtures; `schedule_pages` maps a page token to a getSchedule payload."""
    pages = schedule_pages or {None: load("get_schedule.json")}

    def handler(request: httpx.Request) -> httpx.Response:
        if on_request:
            on_request(request)
        endpoint = request.url.path.rsplit("/", 1)[-1]
        if endpoint == "getLeagues":
            return httpx.Response(200, json=load("get_leagues.json"))
        if endpoint == "getTeams":
            return httpx.Response(200, json=load("get_teams.json"))
        if endpoint == "getSchedule":
            return httpx.Response(200, json=pages[request.url.params.get("pageToken")])
        return httpx.Response(404)

    return LolesportsClient(httpx.Client(transport=httpx.MockTransport(handler)), "test-key")


def test_run_sync_fills_the_database(conn: sqlite3.Connection) -> None:
    stats = run_sync(conn, make_client(), ["lec"])

    assert stats.teams == 4
    assert stats.matches == 3
    kc = conn.execute("SELECT * FROM teams WHERE name = 'Karmine Corp'").fetchone()
    assert (kc["code"], kc["home_league"]) == ("KC", "LEC")
    roster = conn.execute("SELECT count(*) FROM players WHERE team_id = ?", (kc["id"],))
    assert roster.fetchone()[0] == 5

    completed = conn.execute(
        "SELECT * FROM matches WHERE state = 'completed' ORDER BY start_time DESC"
    ).fetchone()
    assert "Karmine Corp" in (completed["team1_name"], completed["team2_name"])
    assert completed["best_of"] in (3, 5)
    assert {completed["team1_outcome"], completed["team2_outcome"]} == {"win", "loss"}
    assert completed["start_time"].endswith("+00:00")


def test_run_sync_is_idempotent(conn: sqlite3.Connection) -> None:
    run_sync(conn, make_client(), ["lec"])
    run_sync(conn, make_client(), ["lec"])

    assert conn.execute("SELECT count(*) FROM teams").fetchone()[0] == 4
    assert conn.execute("SELECT count(*) FROM matches").fetchone()[0] == 3


def test_run_sync_follows_newer_pages(conn: sqlite3.Connection) -> None:
    first = load("get_schedule.json")
    first["data"]["schedule"]["pages"]["newer"] = "next"
    second = load("get_schedule.json")
    for event in second["data"]["schedule"]["events"]:
        event["match"]["id"] += "-newer"

    stats = run_sync(conn, make_client({None: first, "next": second}), ["lec"])

    assert stats.matches == 6


def test_run_sync_skips_unknown_leagues(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    requested: list[str] = []
    client = make_client(on_request=lambda r: requested.append(r.url.params.get("leagueId", "")))

    with caplog.at_level(logging.WARNING):
        run_sync(conn, client, ["lec", "not-a-league"])

    assert "not-a-league" in caplog.text
    assert [league_id for league_id in requested if league_id] == [LEC_ID]


def test_run_sync_leaves_database_untouched_on_api_error(conn: sqlite3.Connection) -> None:
    run_sync(conn, make_client(), ["lec"])

    def fail_on_schedule(request: httpx.Request) -> None:
        if request.url.path.endswith("getSchedule"):
            raise httpx.ConnectError("boom")

    with pytest.raises(httpx.ConnectError):
        run_sync(conn, make_client(on_request=fail_on_schedule), ["lec"])

    assert conn.execute("SELECT count(*) FROM teams").fetchone()[0] == 4
    assert conn.execute("SELECT count(*) FROM matches").fetchone()[0] == 3


def test_to_match_record_skips_events_without_two_teams() -> None:
    event = Event.model_validate(load("get_schedule.json")["data"]["schedule"]["events"][0])
    assert event.match is not None
    single_team = event.model_copy(
        update={"match": event.match.model_copy(update={"teams": event.match.teams[:1]})}
    )

    assert to_match_record(event) is not None
    assert to_match_record(single_team) is None
    assert to_match_record(event.model_copy(update={"match": None})) is None


def test_to_match_record_only_sets_best_of_for_best_of_formats() -> None:
    event = Event.model_validate(load("get_schedule.json")["data"]["schedule"]["events"][0])
    assert event.match is not None
    play_all = event.model_copy(
        update={
            "match": event.match.model_copy(
                update={"strategy": event.match.strategy.model_copy(update={"type": "playAll"})}
            )
        }
    )

    record = to_match_record(play_all)

    assert record is not None
    assert record.best_of is None


def test_run_sync_deletes_cancelled_upcoming_matches(conn: sqlite3.Connection) -> None:
    def lec_event(match_id: str, start_time: str, state: str) -> dict[str, Any]:
        template = load("get_schedule.json")["data"]["schedule"]["events"][0]
        return {
            **template,
            "startTime": start_time,
            "state": state,
            "match": {**template["match"], "id": match_id},
        }

    def schedule_with(*events: dict[str, Any]) -> dict[str, Any]:
        payload = load("get_schedule.json")
        payload["data"]["schedule"]["events"] = list(events)
        return payload

    first = lec_event("first", "2026-10-01T15:00:00Z", "unstarted")
    cancelled = lec_event("cancelled", "2026-10-02T15:00:00Z", "unstarted")
    last = lec_event("last", "2026-10-03T15:00:00Z", "unstarted")
    run_sync(conn, make_client({None: schedule_with(first, cancelled, last)}), ["lec"])

    stats = run_sync(conn, make_client({None: schedule_with(first, last)}), ["lec"])

    assert stats.deleted_matches == 1
    ids = {row["id"] for row in conn.execute("SELECT id FROM matches")}
    assert ids == {"first", "last"}


def test_run_sync_keeps_completed_matches_missing_from_the_source(
    conn: sqlite3.Connection,
) -> None:
    run_sync(conn, make_client(), ["lec"])
    schedule = load("get_schedule.json")
    events = schedule["data"]["schedule"]["events"]
    completed = next(e for e in events if e["state"] == "completed")
    events.remove(completed)

    stats = run_sync(conn, make_client({None: schedule}), ["lec"])

    assert stats.deleted_matches == 0
    assert conn.execute("SELECT count(*) FROM matches").fetchone()[0] == 3


def test_to_team_records_skips_duplicate_teams_and_players(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw = load("get_teams.json")["data"]["teams"]
    kc = Team.model_validate(next(t for t in raw if t["slug"] == "karmine-corp"))
    kc_with_duplicate_player = kc.model_copy(update={"players": [*kc.players, kc.players[0]]})

    with caplog.at_level(logging.WARNING):
        records = to_team_records([kc_with_duplicate_player, kc])

    assert len(records) == 1
    assert len(records[0].players) == len(kc.players)
    assert "Duplicate player" in caplog.text
    assert "Duplicate team" in caplog.text


def test_run_sync_survives_duplicates_in_the_source(conn: sqlite3.Connection) -> None:
    teams = load("get_teams.json")
    teams["data"]["teams"].append(teams["data"]["teams"][0])

    def handler(request: httpx.Request) -> httpx.Response:
        endpoint = request.url.path.rsplit("/", 1)[-1]
        payloads = {
            "getLeagues": load("get_leagues.json"),
            "getTeams": teams,
            "getSchedule": load("get_schedule.json"),
        }
        return httpx.Response(200, json=payloads[endpoint])

    client = LolesportsClient(httpx.Client(transport=httpx.MockTransport(handler)), "key")

    stats = run_sync(conn, client, ["lec"])

    assert stats.teams == 4


def test_main_requires_the_lolesports_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = Settings(_env_file=None, lolesports_api_key=None, sqlite_path=tmp_path / "db")
    monkeypatch.setattr(sync, "get_settings", lambda: settings)

    with pytest.raises(SystemExit, match="LOLESPORTS_API_KEY"):
        sync.main()


def test_main_logs_at_info_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = Settings(_env_file=None, lolesports_api_key=None, sqlite_path=tmp_path / "db")
    monkeypatch.setattr(sync, "get_settings", lambda: settings)
    calls: list[tuple[str, Path | None]] = []
    monkeypatch.setattr(sync, "setup_logging", lambda level, file: calls.append((level, file)))

    with pytest.raises(SystemExit):
        sync.main()

    assert calls == [("INFO", None)]


class FakeRosters:
    """Leaguepedia stand-in: gives Karmine Corp a one-player roster and a coach."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.asked: list[str] = []

    def fetch_players(self, teams: Iterable[str]) -> list[LeaguepediaPlayer]:
        if self.fail:
            raise LeaguepediaError("Still rate limited after several retries")
        self.asked.extend(teams)
        rows = [("Caliste", "Bot"), ("Reapered", "Coach")]
        return [
            LeaguepediaPlayer.model_validate(
                {
                    "Page": player_id,
                    "ID": player_id,
                    "Name": f"{player_id} Real",
                    "Team": "Karmine Corp",
                    "Role": role,
                    "Country": "France",
                }
            )
            for player_id, role in rows
            if "Karmine Corp" in self.asked
        ]

    def fetch_teams_by_short(self, codes: Iterable[str]) -> list[LeaguepediaTeam]:
        return []

    def fetch_roster_joins(self, teams: Iterable[str]) -> list[LeaguepediaRosterJoin]:
        return []

    def fetch_tournament_rosters(
        self, teams: Iterable[str], since: date
    ) -> list[LeaguepediaTournamentPlayer]:
        return []


def test_run_sync_enriches_rosters_of_teams_with_matches(conn: sqlite3.Connection) -> None:
    rosters = FakeRosters()

    run_sync(conn, make_client(), ["lec"], rosters)

    assert "Karmine Corp" in rosters.asked
    assert "TBD" not in rosters.asked
    players = conn.execute(
        "SELECT p.summoner_name, p.country FROM players p JOIN teams t ON t.id = p.team_id"
        " WHERE t.name = 'Karmine Corp'"
    ).fetchall()
    assert [tuple(row) for row in players] == [("Caliste", "France")]
    staff = conn.execute("SELECT name, role FROM staff").fetchall()
    assert [tuple(row) for row in staff] == [("Reapered", "Coach")]


def test_run_sync_keeps_lolesports_rosters_when_leaguepedia_fails(
    conn: sqlite3.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        stats = run_sync(conn, make_client(), ["lec"], FakeRosters(fail=True))

    assert stats.teams == 4
    kc_players = conn.execute(
        "SELECT count(*) FROM players p JOIN teams t ON t.id = p.team_id"
        " WHERE t.name = 'Karmine Corp'"
    ).fetchone()[0]
    assert kc_players == 5
    assert "keeping the lolesports rosters" in caplog.text


def test_make_roster_source_needs_credentials(settings: Settings) -> None:
    assert sync.make_roster_source(settings) is None


def test_make_roster_source_survives_a_failed_login(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def failing_login(_settings: Settings) -> object:
        raise ConnectionError("wiki down")

    monkeypatch.setattr(leaguepedia, "make_client", failing_login)
    settings = Settings(
        _env_file=None, leaguepedia_bot_username="Me@bot", leaguepedia_bot_password="secret"
    )

    with caplog.at_level(logging.ERROR):
        assert sync.make_roster_source(settings) is None
    assert "Leaguepedia login failed" in caplog.text


def test_run_sync_passes_the_aliases(conn: sqlite3.Connection) -> None:
    class AliasWiki(FakeRosters):
        def fetch_players(self, teams: Iterable[str]) -> list[LeaguepediaPlayer]:
            self.asked.extend(teams)
            return []

    rosters = AliasWiki()

    run_sync(conn, make_client(), ["lec"], rosters, {"Karmine Corp": "KC Wiki Page"})

    assert "KC Wiki Page" in rosters.asked
