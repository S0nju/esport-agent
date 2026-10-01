from collections.abc import Mapping
from datetime import date
from typing import Any

import pytest

from esport_agent.config import Settings
from esport_agent.data import leaguepedia
from esport_agent.data.leaguepedia import (
    MAX_RATE_LIMIT_RETRIES,
    MIN_REQUEST_INTERVAL_SECONDS,
    PAGE_SIZE,
    RATE_LIMIT_WAIT_SECONDS,
    TEAMS_PER_QUERY,
    LeaguepediaClient,
    LeaguepediaError,
    cargo_quote,
)


class RateLimitedError(Exception):
    """Mimics mwclient's APIError, which carries the error `code`."""

    code = "ratelimited"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeApi:
    """Plays back responses (or exceptions) and records the requests."""

    # Any: decoded JSON payloads, shaped like Leaguepedia's, validated by the client.
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def __call__(self, action: str, **params: object) -> Any:
        assert action == "cargoquery"
        self.calls.append(params)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def page(*rows: Mapping[str, str | None]) -> dict[str, Any]:
    return {"cargoquery": [{"title": dict(row)} for row in rows], "limits": {"cargoquery": 500}}


def player_row(player_id: str, team: str, role: str, **extra: str) -> dict[str, str]:
    row = {
        "ID": player_id,
        "Name": f"{player_id} Real Name",
        "Team": team,
        "Role": role,
        "Country": "France",
        "IsSubstitute": "0",
    }
    row.update(extra)
    return row


def make_client(api: FakeApi) -> tuple[LeaguepediaClient, FakeClock]:
    clock = FakeClock()
    return LeaguepediaClient(api, sleep=clock.sleep, clock=clock.time), clock


def test_query_sends_the_cargo_parameters() -> None:
    api = FakeApi(page({"Name": "Karmine Corp"}))
    client, _ = make_client(api)

    rows = client.query("Teams", ["Name", "Short"], where='Short="KC"', order_by="Name")

    assert rows == [{"Name": "Karmine Corp"}]
    assert api.calls == [
        {
            "tables": "Teams",
            "fields": "Name,Short",
            "where": 'Short="KC"',
            "order_by": "Name",
            "limit": PAGE_SIZE,
            "offset": 0,
        }
    ]


def test_query_follows_pages_itself() -> None:
    full_page = page(*({"Name": f"Team {i}"} for i in range(PAGE_SIZE)))
    api = FakeApi(full_page, page({"Name": "Last team"}))
    client, _ = make_client(api)

    rows = client.query("Teams", ["Name"])

    assert len(rows) == PAGE_SIZE + 1
    assert [call["offset"] for call in api.calls] == [0, PAGE_SIZE]


def test_query_follows_pages_capped_by_the_server() -> None:
    def capped_page(count: int) -> dict[str, Any]:
        rows = [{"title": {"Name": f"Team {i}"}} for i in range(count)]
        return {"cargoquery": rows, "limits": {"cargoquery": 50}}

    api = FakeApi(capped_page(50), capped_page(50), capped_page(7))
    client, _ = make_client(api)

    rows = client.query("Teams", ["Name"])

    assert len(rows) == 107
    assert [call["offset"] for call in api.calls] == [0, 50, 100]


def test_requests_are_paced() -> None:
    api = FakeApi(page(), page(), page())
    client, clock = make_client(api)

    client.query("Teams", ["Name"])
    clock.now += 5  # Some work between two queries.
    client.query("Teams", ["Name"])
    client.query("Teams", ["Name"])

    assert clock.sleeps == [MIN_REQUEST_INTERVAL_SECONDS - 5, MIN_REQUEST_INTERVAL_SECONDS]


def test_rate_limit_waits_then_retries() -> None:
    api = FakeApi(RateLimitedError(), RateLimitedError(), page({"Name": "Karmine Corp"}))
    client, clock = make_client(api)

    rows = client.query("Teams", ["Name"])

    assert rows == [{"Name": "Karmine Corp"}]
    assert len(api.calls) == 3
    assert clock.sleeps.count(RATE_LIMIT_WAIT_SECONDS) == 2


def test_rate_limit_gives_up_after_several_retries() -> None:
    api = FakeApi(*(RateLimitedError() for _ in range(MAX_RATE_LIMIT_RETRIES + 1)))
    client, _ = make_client(api)

    with pytest.raises(LeaguepediaError, match="rate limited"):
        client.query("Teams", ["Name"])
    assert len(api.calls) == MAX_RATE_LIMIT_RETRIES + 1


def test_other_api_errors_are_not_retried() -> None:
    api = FakeApi(RuntimeError("bad request"))
    client, _ = make_client(api)

    with pytest.raises(RuntimeError, match="bad request"):
        client.query("Teams", ["Name"])
    assert len(api.calls) == 1


def test_unexpected_payload() -> None:
    client, _ = make_client(FakeApi({"error": {"code": "badquery"}}))

    with pytest.raises(LeaguepediaError, match="cargoquery"):
        client.query("Teams", ["Name"])


def test_fetch_players_parses_players_and_staff() -> None:
    api = FakeApi(
        page(
            player_row("Caliste", "Karmine Corp", "Bot"),
            player_row("Reapered", "Karmine Corp", "Coach", Country="South Korea"),
            player_row("Kameto", "Karmine Corp", "Owner", IsSubstitute="", Country=""),
            player_row("Sub", "Karmine Corp", "Mid", IsSubstitute="1"),
        )
    )
    client, _ = make_client(api)

    players = client.fetch_players(["Karmine Corp"])

    assert [(p.id, p.role, p.country, p.is_substitute) for p in players] == [
        ("Caliste", "Bot", "France", False),
        ("Reapered", "Coach", "South Korea", False),
        ("Kameto", "Owner", None, False),
        ("Sub", "Mid", "France", True),
    ]
    assert players[0].name == "Caliste Real Name"
    assert api.calls[0]["where"] == 'Team IN ("Karmine Corp")'
    assert api.calls[0]["fields"] == "ID,Name,Team,Role,Country,IsSubstitute"


def test_fetch_players_splits_many_teams_into_several_queries() -> None:
    teams = [f"Team {i:02d}" for i in range(TEAMS_PER_QUERY + 3)]
    api = FakeApi(page(), page())
    client, _ = make_client(api)

    client.fetch_players([*teams, teams[0]])

    assert len(api.calls) == 2
    second_where = api.calls[1]["where"]
    assert isinstance(second_where, str)
    assert second_where.count('"') == 2 * 3


def test_fetch_players_rejects_unexpected_rows() -> None:
    client, _ = make_client(FakeApi(page({"ID": "Caliste"})))

    with pytest.raises(LeaguepediaError, match="Players"):
        client.fetch_players(["Karmine Corp"])


def test_cargo_quote_escapes_quotes_and_backslashes() -> None:
    assert cargo_quote("Life's Good") == '"Life\'s Good"'
    assert cargo_quote('Team "X"') == '"Team \\"X\\""'
    assert cargo_quote("A\\B") == '"A\\\\B"'


def test_make_client_logs_in_with_the_bot_password(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, object] = {}

    class FakeSite:
        def __init__(self, wiki: str, credentials: object, max_retries: int) -> None:
            created.update(wiki=wiki, credentials=credentials)
            self.client = type("Client", (), {"api": staticmethod(FakeApi(page()))})()

    import mwrogue.esports_client

    monkeypatch.setattr(mwrogue.esports_client, "EsportsClient", FakeSite)
    settings = Settings(
        _env_file=None,
        leaguepedia_bot_username="Me@esport-agent",
        leaguepedia_bot_password="secret",
    )

    client = leaguepedia.make_client(settings)

    assert created["wiki"] == "lol"
    credentials = created["credentials"]
    assert getattr(credentials, "username", None) == "Me@esport-agent"
    assert client.query("Teams", ["Name"]) == []


def test_fetch_teams_by_short() -> None:
    api = FakeApi(
        page(
            {"Page": "Gen.G", "Name": "Gen.G", "Short": "GEN", "IsDisbanded": "0"},
            {"Page": "Old Team (2013)", "Name": "Old Team", "Short": None, "IsDisbanded": "1"},
        )
    )
    client, _ = make_client(api)

    teams = client.fetch_teams_by_short(["GEN", "GEN", "JDG"])

    assert [(t.page, t.name, t.short, t.is_disbanded) for t in teams] == [
        ("Gen.G", "Gen.G", "GEN", False),
        ("Old Team (2013)", "Old Team", "", True),
    ]
    assert api.calls[0]["fields"] == "_pageName=Page,Name,Short,IsDisbanded"
    assert api.calls[0]["where"] == 'Short IN ("GEN", "JDG")'


def test_fetch_tournament_rosters_joins_tournaments() -> None:
    api = FakeApi(
        page(
            {
                "Team": "Joblife",
                "Player": "Ragner",
                "Role": "Top,Bot",
                "Country": "Turkey",
                "Tournament": "LFL 2026 Summer Playoffs",
                "DateStart": "2026-08-12",
                "DateEnd": "2026-09-02",
                "DateStart__precision": "1",
            },
            {
                "Team": "Joblife",
                "Player": "Arkhe",
                "Role": "Coach",
                "Country": "",
                "Tournament": "LFL 2026 Summer Playoffs",
                "DateStart": "2026-08-12",
                "DateEnd": "",
            },
        )
    )
    client, _ = make_client(api)

    rows = client.fetch_tournament_rosters(["Joblife"], since=date(2025, 10, 1))

    assert [(r.player, r.role, r.country, r.end) for r in rows] == [
        ("Ragner", "Top,Bot", "Turkey", date(2026, 9, 2)),
        ("Arkhe", "Coach", None, None),
    ]
    call = api.calls[0]
    assert call["join_on"] == "TP.OverviewPage=T.OverviewPage"
    assert call["where"] == 'TP.Team IN ("Joblife") AND T.DateStart >= "2025-10-01"'
