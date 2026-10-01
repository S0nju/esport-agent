from collections.abc import Iterable
from datetime import date

import pytest

from esport_agent.data.leaguepedia import (
    LeaguepediaPlayer,
    LeaguepediaRosterJoin,
    LeaguepediaTeam,
    LeaguepediaTournamentPlayer,
)
from esport_agent.db import TeamRecord
from esport_agent.rosters import enrich_teams, normalize
from tests.factories import make_player, make_team

TODAY = date(2026, 10, 1)


def wiki_player(
    player_id: str, team: str, role: str, *, country: str | None = "France", page: str = ""
) -> LeaguepediaPlayer:
    return LeaguepediaPlayer.model_validate(
        {
            "Page": page or player_id,
            "ID": player_id,
            "Name": f"{player_id} Real",
            "Team": team,
            "Role": role,
            "Country": country or "",
        }
    )


def join(
    player: str, team: str, joined: str, *, modifier: str = "", status: str = ""
) -> LeaguepediaRosterJoin:
    return LeaguepediaRosterJoin.model_validate(
        {
            "Date": f"{joined} 00:00:00",
            "Player": player,
            "Team": team,
            "RoleModifier": modifier,
            "Status": status,
        }
    )


def wiki_team(
    name: str, short: str, *, page: str | None = None, disbanded: bool = False
) -> LeaguepediaTeam:
    return LeaguepediaTeam.model_validate(
        {
            "Page": page or name,
            "Name": name,
            "Short": short,
            "IsDisbanded": "1" if disbanded else "0",
        }
    )


def registration(
    player: str, team: str, role: str, tournament: str, start: str, end: str = ""
) -> LeaguepediaTournamentPlayer:
    name = player.split(" (")[0]  # The page "Maru (Lee Sang-hun)" is the player "Maru".
    return LeaguepediaTournamentPlayer.model_validate(
        {
            "Team": team,
            "Player": player,
            "DisplayName": name,
            "RealName": f"{name} Real",
            "Role": role,
            "Country": "Turkey",
            "Tournament": tournament,
            "DateStart": start,
            "DateEnd": end,
        }
    )


class FakeWiki:
    """In-memory Leaguepedia: current players, the Teams table and tournament rosters."""

    def __init__(
        self,
        players: Iterable[LeaguepediaPlayer] = (),
        teams: Iterable[LeaguepediaTeam] = (),
        registrations: Iterable[LeaguepediaTournamentPlayer] = (),
        joins: Iterable[LeaguepediaRosterJoin] = (),
    ) -> None:
        self.players = list(players)
        self.teams = list(teams)
        self.registrations = list(registrations)
        self.joins = list(joins)
        self.join_queries: list[list[str]] = []
        self.player_queries: list[list[str]] = []
        self.since: date | None = None

    def fetch_players(self, teams: Iterable[str]) -> list[LeaguepediaPlayer]:
        wanted = list(teams)
        self.player_queries.append(wanted)
        # Like MySQL on Leaguepedia: case- and accent-insensitive comparison.
        keys = {normalize(name) for name in wanted}
        return [p for p in self.players if normalize(p.team) in keys]

    def fetch_teams_by_short(self, codes: Iterable[str]) -> list[LeaguepediaTeam]:
        keys = {normalize(code) for code in codes}
        return [t for t in self.teams if normalize(t.short) in keys]

    def fetch_roster_joins(self, teams: Iterable[str]) -> list[LeaguepediaRosterJoin]:
        wanted = list(teams)
        self.join_queries.append(wanted)
        keys = {normalize(name) for name in wanted}
        return [j for j in self.joins if normalize(j.team) in keys]

    def fetch_tournament_rosters(
        self, teams: Iterable[str], since: date
    ) -> list[LeaguepediaTournamentPlayer]:
        self.since = since
        keys = {normalize(name) for name in teams}
        return [
            r
            for r in self.registrations
            if normalize(r.team) in keys and r.start is not None and r.start >= since
        ]


def lolesports_team(team_id: str, name: str, code: str, status: str = "active") -> TeamRecord:
    return make_team(team_id, (make_player("OldMid", "mid"),), name=name, code=code, status=status)


def test_normalize_ignores_case_accents_and_spaces() -> None:
    assert normalize("  Esprit Shōnen ") == normalize("ESPRIT SHONEN") == "esprit shonen"


def test_match_by_name_replaces_the_roster_and_adds_staff() -> None:
    teams = [lolesports_team("bl", "BILIBILI GAMING", "BLG")]
    wiki = FakeWiki(
        [
            wiki_player("Bin", "Bilibili Gaming", "Top"),
            wiki_player("Sub", "Bilibili Gaming", "Mid", country=None),
            wiki_player("Coach1", "Bilibili Gaming", "Coach"),
            wiki_player("Streamy", "Bilibili Gaming", "Streamer"),
        ],
        joins=[join("Sub", "Bilibili Gaming", "2026-01-10", modifier="Sub")],
    )

    enriched, stats = enrich_teams(teams, {"BILIBILI GAMING": "BLG"}, wiki, today=TODAY)

    team = enriched[0]
    assert team.leaguepedia_name == "Bilibili Gaming"
    assert team.roster_active
    assert [(p.summoner_name, p.role, p.country, p.is_substitute) for p in team.players] == [
        ("Bin", "top", "France", False),
        ("Sub", "mid", None, True),
    ]
    assert team.players[0].first_name == "Bin Real"
    assert [(s.name, s.role) for s in team.staff] == [("Coach1", "Coach")]
    assert (stats.by_name, stats.by_code, stats.unmatched) == (1, 0, ())
    assert wiki.join_queries == [["Bilibili Gaming"]]


def test_match_by_unique_active_code_uses_the_page_name() -> None:
    teams = [lolesports_team("lyon", "LYON", "LYON")]
    wiki = FakeWiki(
        [wiki_player("Inspired", "LYON (2024 American Team)", "Jungle")],
        [
            wiki_team("LYON", "LYON", page="LYON (2024 American Team)"),
            wiki_team("Lyon Gaming", "LYON", page="Lyon Gaming (2013 Team)", disbanded=True),
        ],
    )

    enriched, stats = enrich_teams(teams, {"LYON": "LYON"}, wiki, today=TODAY)

    assert enriched[0].leaguepedia_name == "LYON (2024 American Team)"
    assert [p.summoner_name for p in enriched[0].players] == ["Inspired"]
    assert (stats.by_name, stats.by_code) == (0, 1)
    assert wiki.player_queries == [["LYON"], ["LYON (2024 American Team)"]]


def test_alias_comes_first() -> None:
    teams = [lolesports_team("tl", "Team Liquid Alienware", "TLAW")]
    wiki = FakeWiki(
        [wiki_player("CoreJJ", "Team Liquid", "Support")],
        [wiki_team("Other", "TLAW")],
    )

    enriched, stats = enrich_teams(
        teams,
        {"Team Liquid Alienware": "TLAW"},
        wiki,
        today=TODAY,
        aliases={"Team Liquid Alienware": "Team Liquid", "Untracked": "Elsewhere"},
    )

    assert [p.summoner_name for p in enriched[0].players] == ["CoreJJ"]
    assert (stats.by_alias, stats.by_name, stats.by_code) == (1, 0, 0)


def test_ambiguous_code_is_not_matched() -> None:
    teams = [lolesports_team("x", "X Esports", "XX")]
    wiki = FakeWiki(
        [wiki_player("A", "X One", "Mid"), wiki_player("B", "X Two", "Mid")],
        [wiki_team("X One", "XX"), wiki_team("X Two", "XX")],
    )

    enriched, stats = enrich_teams(teams, {"X Esports": "XX"}, wiki, today=TODAY)

    assert enriched == teams
    assert stats.unmatched == ("X Esports",)


def test_team_without_current_roster_gets_its_last_tournament_roster() -> None:
    teams = [lolesports_team("jl", "Joblife", "JL")]
    wiki = FakeWiki(
        registrations=[
            registration("Ragner", "Joblife", "Top,Bot", "LFL Summer", "2026-07-21", "2026-08-06"),
            registration("Vertigo", "Joblife", "Top", "LFL Summer", "2026-07-21", "2026-08-06"),
            registration("Ragner", "Joblife", "Top", "LFL Playoffs", "2026-08-12", "2026-09-02"),
            registration(
                "Kofte (Turkish Player)",
                "Joblife",
                "Mid",
                "LFL Playoffs",
                "2026-08-12",
                "2026-09-02",
            ),
            registration("Arkhe", "Joblife", "Coach", "LFL Playoffs", "2026-08-12", "2026-09-02"),
        ]
    )

    enriched, stats = enrich_teams(teams, {"Joblife": "JL"}, wiki, today=TODAY)

    team = enriched[0]
    assert team.players[1].first_name == "Kofte Real"
    assert team.staff[0].real_name == "Arkhe Real"
    assert not team.roster_active
    assert team.roster_tournament == "LFL Playoffs"
    assert team.roster_date == date(2026, 9, 2)
    assert [(p.summoner_name, p.role, p.country) for p in team.players] == [
        ("Ragner", "top", "Turkey"),
        ("Kofte", "mid", "Turkey"),
    ]
    assert [(s.name, s.role) for s in team.staff] == [("Arkhe", "Coach")]
    assert stats.last_known == 1
    assert wiki.since == date(2025, 10, 1)


def test_old_or_staff_only_tournaments_are_not_used() -> None:
    teams = [lolesports_team("jl", "Joblife", "JL")]
    wiki = FakeWiki(
        registrations=[
            registration("Ragner", "Joblife", "Top", "LFL 2024", "2024-01-10"),
            registration("Arkhe", "Joblife", "Coach", "LFL Playoffs", "2026-08-12"),
        ]
    )

    enriched, stats = enrich_teams(teams, {"Joblife": "JL"}, wiki, today=TODAY)

    assert enriched == teams
    assert stats.unmatched == ("Joblife",)


def test_team_without_wiki_players_keeps_its_roster() -> None:
    teams = [lolesports_team("kc", "Karmine Corp", "KC")]
    wiki = FakeWiki([wiki_player("Coach1", "Karmine Corp", "Coach")])

    enriched, _ = enrich_teams(teams, {"Karmine Corp": "KC"}, wiki, today=TODAY)

    assert [p.summoner_name for p in enriched[0].players] == ["OldMid"]
    assert [s.name for s in enriched[0].staff] == ["Coach1"]


def test_only_the_best_homonym_is_enriched() -> None:
    old = lolesports_team("old", "Alliance", "ALL", status="archived")
    pro = lolesports_team("pro", "Alliance", "ALL")
    wiki = FakeWiki([wiki_player("Star", "Alliance", "Top")])

    enriched, _ = enrich_teams([old, pro], {"Alliance": "ALL"}, wiki, today=TODAY)

    assert enriched[0] == old
    assert [p.summoner_name for p in enriched[1].players] == ["Star"]


def test_untracked_teams_are_untouched() -> None:
    teams = [lolesports_team("kc", "Karmine Corp", "KC"), lolesports_team("g2", "G2", "G2")]
    wiki = FakeWiki([wiki_player("Caps", "G2", "Mid")])

    enriched, _ = enrich_teams(teams, {"Karmine Corp": "KC"}, wiki, today=TODAY)

    assert enriched[1] == teams[1]


@pytest.mark.parametrize("code", ["", "XX"])
def test_unmatched_teams_are_reported(code: str) -> None:
    teams = [lolesports_team("x", "Nowhere", code)]

    _, stats = enrich_teams(teams, {"Nowhere": code}, FakeWiki(), today=TODAY)

    assert stats.unmatched == ("Nowhere",)


def test_staff_only_page_prefers_the_last_tournament_roster() -> None:
    teams = [lolesports_team("jl", "Joblife", "JL")]
    wiki = FakeWiki(
        [wiki_player("Arkhe", "Joblife", "Coach")],
        registrations=[
            registration("Kofte", "Joblife", "Mid", "LFL Playoffs", "2026-08-12", "2026-09-02"),
        ],
    )

    enriched, stats = enrich_teams(teams, {"Joblife": "JL"}, wiki, today=TODAY)

    assert not enriched[0].roster_active
    assert [p.summoner_name for p in enriched[0].players] == ["Kofte"]
    assert stats.last_known == 1


def test_last_join_status_flags_substitutes_and_leaves_out_away_players() -> None:
    team = "LYON (2024 American Team)"
    teams = [lolesports_team("lyon", "LYON", "LYON")]
    wiki = FakeWiki(
        [
            wiki_player("Castle", team, "Top", page="Castle (Cho Hyeon-seong)"),
            wiki_player("Dhokla", team, "Top"),
            wiki_player("Zamudo", team, "Top"),
            wiki_player("Trial", team, "Mid"),
            wiki_player("Promoted", team, "Jungle"),
            wiki_player("Loaned", team, "Bot"),
            wiki_player("Veteran", team, "Support"),
        ],
        [wiki_team("LYON", "LYON", page=team)],
        joins=[
            # Page names may differ in case from the player's page.
            join("castle (Cho Hyeon-seong)", team, "2026-09-08", modifier="Sub"),
            join("Dhokla", team, "2026-01-13"),
            join("Zamudo", team, "2026-01-12", status="inactive"),
            join("Trial", team, "2026-09-01", status="trial"),
            join("Promoted", team, "2026-02-01", modifier="Sub"),
            join("Promoted", team, "2026-06-01"),
            join("Loaned", team, "2026-03-01", status="loaned_out"),
            join("Dhokla", "Another Team", "2026-09-30", status="inactive"),
        ],
    )

    enriched, stats = enrich_teams(teams, {"LYON": "LYON"}, wiki, today=TODAY)

    assert [(p.summoner_name, p.is_substitute) for p in enriched[0].players] == [
        ("Castle", True),
        ("Dhokla", False),
        ("Trial", True),
        ("Promoted", False),
        ("Veteran", False),
    ]
    assert (stats.substitutes, stats.away) == (2, 2)
    assert wiki.join_queries == [[team]]


def test_no_join_query_without_current_rosters() -> None:
    wiki = FakeWiki()

    enrich_teams([lolesports_team("x", "Nowhere", "")], {"Nowhere": ""}, wiki, today=TODAY)

    assert wiki.join_queries == [[]]


def test_last_known_roster_keeps_its_staff_when_only_new_coaches_are_listed() -> None:
    teams = [lolesports_team("jl", "Joblife", "JL")]
    wiki = FakeWiki(
        [wiki_player("NewCoach", "Joblife", "Coach")],
        registrations=[
            registration("Kofte", "Joblife", "Mid", "LFL Playoffs", "2026-08-12", "2026-09-02"),
            registration("Arkhe", "Joblife", "Coach", "LFL Playoffs", "2026-08-12", "2026-09-02"),
        ],
    )

    enriched, _ = enrich_teams(teams, {"Joblife": "JL"}, wiki, today=TODAY)

    team = enriched[0]
    assert not team.roster_active
    assert [p.summoner_name for p in team.players] == ["Kofte"]
    assert [s.name for s in team.staff] == ["Arkhe"]
