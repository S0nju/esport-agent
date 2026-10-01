from collections.abc import Iterable

import pytest

from esport_agent.data.leaguepedia import LeaguepediaPlayer, LeaguepediaTeam
from esport_agent.db import TeamRecord
from esport_agent.rosters import enrich_teams, normalize
from tests.factories import make_player, make_team


def wiki_player(
    player_id: str, team: str, role: str, *, sub: bool = False, country: str | None = "France"
) -> LeaguepediaPlayer:
    return LeaguepediaPlayer.model_validate(
        {
            "ID": player_id,
            "Name": f"{player_id} Real",
            "Team": team,
            "Role": role,
            "Country": country or "",
            "IsSubstitute": "1" if sub else "0",
        }
    )


def wiki_team(name: str, short: str, *, disbanded: bool = False) -> LeaguepediaTeam:
    return LeaguepediaTeam.model_validate(
        {"Name": name, "Short": short, "IsDisbanded": "1" if disbanded else "0"}
    )


class FakeWiki:
    """In-memory Leaguepedia: players by wiki team name, and the Teams table."""

    def __init__(
        self, players: Iterable[LeaguepediaPlayer], teams: Iterable[LeaguepediaTeam] = ()
    ) -> None:
        self.players = list(players)
        self.teams = list(teams)
        self.player_queries: list[list[str]] = []

    def fetch_players(self, teams: Iterable[str]) -> list[LeaguepediaPlayer]:
        wanted = list(teams)
        self.player_queries.append(wanted)
        # Like MySQL on Leaguepedia: case- and accent-insensitive comparison.
        keys = {normalize(name) for name in wanted}
        return [p for p in self.players if normalize(p.team) in keys]

    def fetch_teams_by_short(self, codes: Iterable[str]) -> list[LeaguepediaTeam]:
        keys = {normalize(code) for code in codes}
        return [t for t in self.teams if normalize(t.short) in keys]


def lolesports_team(team_id: str, name: str, code: str, status: str = "active") -> TeamRecord:
    return make_team(team_id, (make_player("OldMid", "mid"),), name=name, code=code, status=status)


def test_normalize_ignores_case_accents_and_spaces() -> None:
    assert normalize("  Esprit Shōnen ") == normalize("ESPRIT SHONEN") == "esprit shonen"


def test_match_by_name_replaces_the_roster_and_adds_staff() -> None:
    teams = [lolesports_team("bl", "BILIBILI GAMING", "BLG")]
    wiki = FakeWiki(
        [
            wiki_player("Bin", "Bilibili Gaming", "Top"),
            wiki_player("Sub", "Bilibili Gaming", "Mid", sub=True, country=None),
            wiki_player("Coach1", "Bilibili Gaming", "Coach"),
            wiki_player("Streamy", "Bilibili Gaming", "Streamer"),
        ]
    )

    enriched, stats = enrich_teams(teams, {"BILIBILI GAMING": "BLG"}, wiki)

    team = enriched[0]
    assert team.leaguepedia_name == "Bilibili Gaming"
    assert [(p.summoner_name, p.role, p.country, p.is_substitute) for p in team.players] == [
        ("Bin", "top", "France", False),
        ("Sub", "mid", None, True),
    ]
    assert team.players[0].first_name == "Bin Real"
    assert [(s.name, s.role) for s in team.staff] == [("Coach1", "Coach")]
    assert (stats.by_name, stats.by_code, stats.unmatched) == (1, 0, ())


def test_match_by_unique_active_code() -> None:
    teams = [lolesports_team("gen", "Gen.G Esports", "GEN")]
    wiki = FakeWiki(
        [wiki_player("Chovy", "Gen.G", "Mid")],
        [wiki_team("Gen.G", "GEN"), wiki_team("Old Gen", "GEN", disbanded=True)],
    )

    enriched, stats = enrich_teams(teams, {"Gen.G Esports": "GEN"}, wiki)

    assert enriched[0].leaguepedia_name == "Gen.G"
    assert [p.summoner_name for p in enriched[0].players] == ["Chovy"]
    assert (stats.by_name, stats.by_code) == (0, 1)
    assert wiki.player_queries == [["Gen.G Esports"], ["Gen.G"]]


def test_ambiguous_code_is_not_matched() -> None:
    teams = [lolesports_team("x", "X Esports", "XX")]
    wiki = FakeWiki(
        [wiki_player("A", "X One", "Mid"), wiki_player("B", "X Two", "Mid")],
        [wiki_team("X One", "XX"), wiki_team("X Two", "XX")],
    )

    enriched, stats = enrich_teams(teams, {"X Esports": "XX"}, wiki)

    assert enriched == teams
    assert stats.unmatched == ("X Esports",)


def test_team_without_wiki_players_keeps_its_roster() -> None:
    teams = [lolesports_team("kc", "Karmine Corp", "KC")]
    wiki = FakeWiki([wiki_player("Coach1", "Karmine Corp", "Coach")])

    enriched, _ = enrich_teams(teams, {"Karmine Corp": "KC"}, wiki)

    assert [p.summoner_name for p in enriched[0].players] == ["OldMid"]
    assert [s.name for s in enriched[0].staff] == ["Coach1"]


def test_only_the_best_homonym_is_enriched() -> None:
    old = lolesports_team("old", "Alliance", "ALL", status="archived")
    pro = lolesports_team("pro", "Alliance", "ALL")
    wiki = FakeWiki([wiki_player("Star", "Alliance", "Top")])

    enriched, _ = enrich_teams([old, pro], {"Alliance": "ALL"}, wiki)

    assert enriched[0] == old
    assert [p.summoner_name for p in enriched[1].players] == ["Star"]


def test_untracked_teams_are_untouched() -> None:
    teams = [lolesports_team("kc", "Karmine Corp", "KC"), lolesports_team("g2", "G2", "G2")]
    wiki = FakeWiki([wiki_player("Caps", "G2", "Mid")])

    enriched, _ = enrich_teams(teams, {"Karmine Corp": "KC"}, wiki)

    assert enriched[1] == teams[1]


@pytest.mark.parametrize("code", ["", "XX"])
def test_unmatched_teams_are_reported(code: str) -> None:
    teams = [lolesports_team("x", "Nowhere", code)]

    _, stats = enrich_teams(teams, {"Nowhere": code}, FakeWiki([]))

    assert stats.unmatched == ("Nowhere",)
