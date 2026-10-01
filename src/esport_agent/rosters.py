"""Enrich lolesports teams with Leaguepedia rosters, staff and countries.

The two sources name teams differently, so each lolesports team is matched to a
Leaguepedia team through a cascade, from the safest rule to the riskiest:

1. same name, ignoring case and accents ("BILIBILI GAMING" is "Bilibili Gaming");
2. same short code, only if exactly one active Leaguepedia team uses it ("GEN" is "Gen.G");
3. otherwise no match, and the lolesports roster is kept.

A missing match costs little, a wrong one shows another team's roster: ambiguous cases are
never matched. Leaguepedia is the reference for the rosters it has (starters, substitutes,
staff), lolesports for everything else.
"""

import logging
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Protocol

from esport_agent.data.leaguepedia import LeaguepediaPlayer, LeaguepediaTeam
from esport_agent.db import PlayerRecord, StaffRecord, TeamRecord

logger = logging.getLogger(__name__)

LANE_ROLES = {"Top": "top", "Jungle": "jungle", "Mid": "mid", "Bot": "bottom", "Support": "support"}
"""Leaguepedia player roles, mapped to the roles stored in the database."""

NON_STAFF_ROLES = frozenset({"Streamer", "Caster", "Creator", "Content Creator", "Influencer"})
"""Members listed on a team page who are not part of its sports staff."""


class RosterSource(Protocol):
    """What the enrichment needs from Leaguepedia (the real client or a fake in tests)."""

    def fetch_players(self, teams: Iterable[str]) -> list[LeaguepediaPlayer]: ...

    def fetch_teams_by_short(self, codes: Iterable[str]) -> list[LeaguepediaTeam]: ...


@dataclass(frozen=True)
class EnrichmentStats:
    by_name: int
    by_code: int
    unmatched: tuple[str, ...]


def normalize(name: str) -> str:
    """Lower-case and strip accents and surrounding spaces: "Esprit Shōnen" -> "esprit shonen"."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().strip()


def enrich_teams(
    teams: list[TeamRecord], tracked: Mapping[str, str], source: RosterSource
) -> tuple[list[TeamRecord], EnrichmentStats]:
    """Replace the roster of the `tracked` teams (name -> short code) with Leaguepedia's.

    Only tracked teams are looked up, to keep the number of requests low. Teams that are
    not matched, or whose Leaguepedia page lists no player, keep their lolesports roster.
    """
    players_by_team = _group(source.fetch_players(tracked))
    matched: dict[str, str] = {}  # lolesports name -> normalized Leaguepedia name
    for name in tracked:
        if normalize(name) in players_by_team:
            matched[name] = normalize(name)
    by_name = len(matched)

    unmatched = [name for name in tracked if name not in matched and tracked[name]]
    if unmatched:
        by_code = _match_by_code(unmatched, tracked, source)
        players_by_team.update(_group(source.fetch_players(by_code.values())))
        for name, wiki_name in by_code.items():
            if normalize(wiki_name) in players_by_team:
                matched[name] = normalize(wiki_name)

    best = _best_record_by_name(teams)
    enriched = {
        best[name].id: _with_leaguepedia_roster(best[name], players_by_team[key])
        for name, key in matched.items()
        if name in best
    }
    stats = EnrichmentStats(
        by_name=by_name,
        by_code=len(matched) - by_name,
        unmatched=tuple(sorted(set(tracked) - set(matched))),
    )
    return [enriched.get(team.id, team) for team in teams], stats


def _group(players: Iterable[LeaguepediaPlayer]) -> dict[str, list[LeaguepediaPlayer]]:
    groups: dict[str, list[LeaguepediaPlayer]] = defaultdict(list)
    for player in players:
        groups[normalize(player.team)].append(player)
    return dict(groups)


def _match_by_code(
    names: Iterable[str], tracked: Mapping[str, str], source: RosterSource
) -> dict[str, str]:
    """Return lolesports name -> Leaguepedia name, when the code is used by one active team."""
    names = list(names)
    wiki_teams = source.fetch_teams_by_short(tracked[name] for name in names)
    active_by_code: dict[str, list[LeaguepediaTeam]] = defaultdict(list)
    for team in wiki_teams:
        if not team.is_disbanded:
            active_by_code[normalize(team.short)].append(team)
    result: dict[str, str] = {}
    for name in names:
        candidates = active_by_code.get(normalize(tracked[name]), [])
        if len(candidates) == 1:
            result[name] = candidates[0].name
        elif len(candidates) > 1:
            logger.info("Code %s of %s is ambiguous on Leaguepedia, skipped", tracked[name], name)
    return result


def _best_record_by_name(teams: Iterable[TeamRecord]) -> dict[str, TeamRecord]:
    """Among homonyms, keep the active team with a league and the largest roster."""
    best: dict[str, TeamRecord] = {}
    for team in teams:
        current = best.get(team.name)
        if current is None or _rank(team) > _rank(current):
            best[team.name] = team
    return best


def _rank(team: TeamRecord) -> tuple[bool, bool, int]:
    return team.status == "active", team.home_league is not None, len(team.players)


def _with_leaguepedia_roster(team: TeamRecord, members: list[LeaguepediaPlayer]) -> TeamRecord:
    players: dict[str, PlayerRecord] = {}
    staff: dict[tuple[str, str], StaffRecord] = {}
    for member in members:
        if member.role in LANE_ROLES:
            players.setdefault(
                member.id,
                PlayerRecord(
                    id=member.id,
                    summoner_name=member.id,
                    first_name=member.name,
                    last_name="",
                    role=LANE_ROLES[member.role],
                    country=member.country,
                    is_substitute=member.is_substitute,
                ),
            )
        elif member.role not in NON_STAFF_ROLES:
            staff.setdefault(
                (member.id, member.role),
                StaffRecord(
                    name=member.id, real_name=member.name, role=member.role, country=member.country
                ),
            )
    return replace(
        team,
        # A wiki page that lists no player yet keeps the lolesports roster.
        players=tuple(players.values()) if players else team.players,
        staff=tuple(staff.values()),
        leaguepedia_name=members[0].team,
    )
