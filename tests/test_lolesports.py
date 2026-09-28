from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from esport_agent.data.lolesports import BASE_URL, LolesportsClient, LolesportsFormatError

FIXTURES = Path(__file__).parent / "fixtures" / "lolesports"
API_KEY = "test-key"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[LolesportsClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    http = httpx.Client(transport=httpx.MockTransport(recording_handler))
    return LolesportsClient(http, API_KEY), requests


def serve(name: str) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(200, content=fixture_bytes(name))


def test_get_leagues() -> None:
    client, requests = make_client(serve("get_leagues.json"))

    leagues = client.get_leagues()

    assert {league.slug for league in leagues} == {"lec", "lfl", "worlds"}
    lec = next(league for league in leagues if league.slug == "lec")
    assert lec.id == "98767991302996019"
    assert lec.region == "EMEA"
    assert str(requests[0].url).startswith(f"{BASE_URL}/getLeagues")
    assert requests[0].headers["x-api-key"] == API_KEY
    assert requests[0].url.params["hl"] == "en-US"


def test_get_teams_parses_rosters() -> None:
    client, requests = make_client(serve("get_teams.json"))

    teams = client.get_teams()

    assert "id" not in requests[0].url.params
    kc = next(team for team in teams if team.slug == "karmine-corp")
    assert kc.name == "Karmine Corp"
    assert kc.code == "KC"
    assert kc.status == "active"
    assert kc.home_league is not None
    assert kc.home_league.name == "LEC"
    assert {player.role for player in kc.players} == {"top", "jungle", "mid", "bottom", "support"}
    assert any(player.summoner_name == "Caliste" for player in kc.players)


def test_get_teams_handles_missing_home_league_and_roleless_players() -> None:
    client, _ = make_client(serve("get_teams.json"))

    teams = client.get_teams()

    assert any(team.home_league is None and team.players == [] for team in teams)
    assert any(player.role == "none" for team in teams for player in team.players)


def test_get_teams_by_slug() -> None:
    client, requests = make_client(serve("get_teams.json"))

    client.get_teams("karmine-corp")

    assert requests[0].url.params["id"] == "karmine-corp"


def test_get_schedule() -> None:
    client, requests = make_client(serve("get_schedule.json"))

    schedule = client.get_schedule("98767991302996019")

    assert requests[0].url.params["leagueId"] == "98767991302996019"
    assert "pageToken" not in requests[0].url.params
    assert schedule.pages.older is not None
    assert schedule.pages.newer is None

    completed, *_, unstarted = schedule.events
    assert completed.state == "completed"
    assert completed.start_time.tzinfo is not None
    assert completed.league.slug == "lec"
    assert completed.match is not None
    assert completed.match.strategy.count in (3, 5)
    results = [team.result for team in completed.match.teams]
    assert all(result is not None and result.outcome in ("win", "loss") for result in results)

    assert unstarted.state == "unstarted"
    assert unstarted.start_time > datetime(2026, 1, 1, tzinfo=UTC)
    assert unstarted.match is not None
    assert [team.name for team in unstarted.match.teams] == ["TBD", "TBD"]
    assert all(team.result is None for team in unstarted.match.teams)


def test_get_schedule_with_page_token() -> None:
    client, requests = make_client(serve("get_schedule.json"))

    client.get_schedule("123", page_token="token")

    assert requests[0].url.params["pageToken"] == "token"


def test_http_error_is_raised() -> None:
    client, _ = make_client(lambda request: httpx.Response(403, json={"message": "Forbidden"}))

    with pytest.raises(httpx.HTTPStatusError):
        client.get_leagues()


def test_unexpected_format_raises_format_error() -> None:
    client, _ = make_client(lambda request: httpx.Response(200, json={"data": {"other": []}}))

    with pytest.raises(LolesportsFormatError, match="getLeagues"):
        client.get_leagues()
