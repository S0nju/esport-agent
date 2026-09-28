import json
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from esport_agent.data.lolesports import Event, LolesportsClient
from esport_agent.sync import run_sync, to_match_record

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
