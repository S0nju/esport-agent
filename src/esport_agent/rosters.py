"""Enrich lolesports teams with Leaguepedia rosters, staff and countries.

The two sources name teams differently, so each lolesports team is matched to a
Leaguepedia team page through a cascade, from the safest rule to the riskiest:

0. an alias from the settings, decided by a human;
1. same name, ignoring case and accents ("BILIBILI GAMING" is "Bilibili Gaming");
2. same short code, only if exactly one active Leaguepedia team uses it ("GEN" is the
   page "Gen.G", "LYON" the page "LYON (2024 American Team)");
3. otherwise no match, and the lolesports roster is kept.

A missing match costs little, a wrong one shows another team's roster: ambiguous cases are
never matched. A matched team with no current roster on the wiki (between seasons, after
its players left) gets the roster it registered for its last tournament, if that
tournament started less than a year ago, flagged as no longer active, with the staff of
that tournament: new coaches announced before any player do not replace it, since a
roster is first about players.

Current players are flagged as substitutes, or left out when inactive or loaned out, from
the status of their last join to the team (`RosterChanges`).
"""

import logging
import unicodedata
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Protocol

from esport_agent.data.leaguepedia import (
    LeaguepediaPlayer,
    LeaguepediaRosterJoin,
    LeaguepediaTeam,
    LeaguepediaTournamentPlayer,
)
from esport_agent.db import PlayerRecord, StaffRecord, TeamRecord

logger = logging.getLogger(__name__)

LANE_ROLES = {"Top": "top", "Jungle": "jungle", "Mid": "mid", "Bot": "bottom", "Support": "support"}
"""Leaguepedia player roles, mapped to the roles stored in the database."""

NON_STAFF_ROLES = frozenset({"Streamer", "Caster", "Creator", "Content Creator", "Influencer"})
"""Members listed on a team page who are not part of its sports staff."""

SUBSTITUTE_STATUSES = frozenset({"temp_sub", "official_sub", "trial"})
"""Join statuses of players kept as substitutes (besides the "Sub" role modifier)."""

AWAY_STATUSES = frozenset({"inactive", "loaned_out", "opportunities"})
"""Join statuses of players still listed by the team who do not play for it."""

LAST_ROSTER_MAX_AGE = timedelta(days=365)
"""A last known roster older than this is ignored: it could belong to an old homonym."""


class RosterSource(Protocol):
    """What the enrichment needs from Leaguepedia (the real client or a fake in tests)."""

    def fetch_players(self, teams: Iterable[str]) -> list[LeaguepediaPlayer]: ...

    def fetch_teams_by_short(self, codes: Iterable[str]) -> list[LeaguepediaTeam]: ...

    def fetch_roster_joins(self, teams: Iterable[str]) -> list[LeaguepediaRosterJoin]: ...

    def fetch_tournament_rosters(
        self, teams: Iterable[str], since: date
    ) -> list[LeaguepediaTournamentPlayer]: ...


@dataclass(frozen=True)
class EnrichmentStats:
    by_alias: int = 0
    by_name: int = 0
    by_code: int = 0
    last_known: int = 0
    substitutes: int = 0
    away: int = 0
    """Players left out because they are inactive or loaned out."""
    unmatched: tuple[str, ...] = field(default=())


def normalize(name: str) -> str:
    """Lower-case and strip accents and surrounding spaces: "Esprit Shōnen" -> "esprit shonen"."""
    decomposed = unicodedata.normalize("NFKD", name)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().strip()


def enrich_teams(
    teams: list[TeamRecord],
    tracked: Mapping[str, str],
    source: RosterSource,
    *,
    today: date,
    aliases: Mapping[str, str] | None = None,
) -> tuple[list[TeamRecord], EnrichmentStats]:
    """Replace the roster of the `tracked` teams (name -> short code) with Leaguepedia's.

    Only tracked teams are looked up, to keep the number of requests low. `aliases` maps a
    lolesports name to a Leaguepedia page name. Teams that are not matched keep their
    lolesports roster.
    """
    aliases = {name: page for name, page in (aliases or {}).items() if name in tracked}
    method: dict[str, str] = {}  # lolesports name -> how its page was found
    page_of: dict[str, str] = {}  # lolesports name -> Leaguepedia page to use
    for name, page in aliases.items():
        page_of[name], method[name] = page, "alias"

    by_name = [name for name in tracked if name not in page_of]
    current = _group(source.fetch_players(by_name), lambda p: p.team)
    for name in by_name:
        if normalize(name) in current:
            page_of[name], method[name] = name, "name"

    by_code = [name for name in tracked if name not in page_of and tracked[name]]
    for name, page in _match_by_code(by_code, tracked, source).items():
        page_of[name], method[name] = page, "code"
    extra_pages = [page_of[n] for n in page_of if method[n] in ("alias", "code")]
    if extra_pages:
        current.update(_group(source.fetch_players(extra_pages), lambda p: p.team))

    enriched: dict[str, TeamRecord] = {}
    best = _best_record_by_name(teams)
    with_current: dict[str, list[LeaguepediaPlayer]] = {}
    without_current = []
    staff_only: dict[str, list[LeaguepediaPlayer]] = {}
    for name in tracked:
        if name not in best:
            continue
        members = current.get(normalize(page_of.get(name, name)), [])
        if any(member.role in LANE_ROLES for member in members):
            with_current[name] = members
        else:
            # No player listed (staff only, or nothing): look for a last known roster.
            without_current.append(name)
            if members:
                staff_only[name] = members

    statuses = _last_joins(source.fetch_roster_joins(m[0].team for m in with_current.values()))
    substitutes = away = 0
    for name, members in with_current.items():
        enriched[name], subs, left_out = _with_current_roster(best[name], members, statuses)
        substitutes += subs
        away += left_out

    last_known = 0
    if without_current:
        pages = {name: page_of.get(name, name) for name in without_current}
        rows = source.fetch_tournament_rosters(pages.values(), since=today - LAST_ROSTER_MAX_AGE)
        by_team = _group(rows, lambda r: r.team)
        for name, page in pages.items():
            last = _last_tournament(by_team.get(normalize(page), []))
            if last:
                enriched[name] = _with_last_known_roster(best[name], last)
                method.setdefault(name, "name")
                last_known += 1
            elif name in staff_only:
                # Still worth having the staff, next to the lolesports players.
                enriched[name], _, _ = _with_current_roster(best[name], staff_only[name], {})

    counts = defaultdict(int, dict.fromkeys(("alias", "name", "code"), 0))
    for name in enriched:
        counts[method.get(name, "name")] += 1
    stats = EnrichmentStats(
        by_alias=counts["alias"],
        by_name=counts["name"],
        by_code=counts["code"],
        last_known=last_known,
        substitutes=substitutes,
        away=away,
        unmatched=tuple(sorted(set(tracked) - set(enriched))),
    )
    by_id = {record.id: record for record in enriched.values()}
    return [by_id.get(team.id, team) for team in teams], stats


def _group[T](rows: Iterable[T], team_of: Callable[[T], str]) -> dict[str, list[T]]:
    """Group rows by normalized team page name."""
    groups: dict[str, list[T]] = defaultdict(list)
    for row in rows:
        groups[normalize(team_of(row))].append(row)
    return dict(groups)


def _match_by_code(
    names: Iterable[str], tracked: Mapping[str, str], source: RosterSource
) -> dict[str, str]:
    """Return lolesports name -> Leaguepedia page, when the code is used by one active team."""
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
            result[name] = candidates[0].page
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


def _last_joins(
    joins: Iterable[LeaguepediaRosterJoin],
) -> dict[tuple[str, str], LeaguepediaRosterJoin]:
    """Return the most recent join of each (team page, player page), normalized."""
    last: dict[tuple[str, str], LeaguepediaRosterJoin] = {}
    for join in joins:
        key = (normalize(join.team), normalize(join.player))
        known = last.get(key)
        if known is None or (join.joined or date.min) > (known.joined or date.min):
            last[key] = join
    return last


def _with_current_roster(
    team: TeamRecord,
    members: list[LeaguepediaPlayer],
    statuses: Mapping[tuple[str, str], LeaguepediaRosterJoin],
) -> tuple[TeamRecord, int, int]:
    """Return the team with the wiki roster, its number of substitutes and of players left out.

    A player without a recorded join (joined before the wiki tracked roster changes) counts
    as a starter.
    """
    players: dict[str, PlayerRecord] = {}
    away = 0
    for member in members:
        if member.role in LANE_ROLES:
            join = statuses.get((normalize(member.team), normalize(member.page)))
            if join and join.status in AWAY_STATUSES:
                away += 1
                continue
            players.setdefault(
                member.id,
                PlayerRecord(
                    id=member.id,
                    summoner_name=member.id,
                    first_name=member.name,
                    last_name="",
                    role=LANE_ROLES[member.role],
                    country=member.country,
                    is_substitute=bool(
                        join and (join.role_modifier == "Sub" or join.status in SUBSTITUTE_STATUSES)
                    ),
                ),
            )
    enriched = replace(
        team,
        # A wiki page that lists no player yet keeps the lolesports roster.
        players=tuple(players.values()) if players else team.players,
        staff=_staff_of(members),
        leaguepedia_name=members[0].team,
    )
    substitutes = sum(p.is_substitute for p in players.values())
    return enriched, substitutes, away


def _staff_of(members: Iterable[LeaguepediaPlayer]) -> tuple[StaffRecord, ...]:
    """Return the sports staff among the members of a team page (no players, no streamers)."""
    staff: dict[tuple[str, str], StaffRecord] = {}
    for member in members:
        if member.role not in LANE_ROLES and member.role not in NON_STAFF_ROLES:
            staff.setdefault(
                (member.id, member.role),
                StaffRecord(
                    name=member.id, real_name=member.name, role=member.role, country=member.country
                ),
            )
    return tuple(staff.values())


def _last_tournament(
    rows: list[LeaguepediaTournamentPlayer],
) -> list[LeaguepediaTournamentPlayer] | None:
    """Return the rows of the most recent tournament, if it lists at least one player."""
    if not rows:
        return None
    latest = max(rows, key=lambda r: (r.start or date.min, r.tournament))
    members = [r for r in rows if r.tournament == latest.tournament]
    if not any(_lane(r.role) for r in members):
        return None
    return members


def _lane(role: str) -> str | None:
    """Return the database role of a tournament role; "Top,Bot" counts as its first lane."""
    first = role.split(",")[0].strip()
    return LANE_ROLES.get(first)


def _with_last_known_roster(
    team: TeamRecord, members: list[LeaguepediaTournamentPlayer]
) -> TeamRecord:
    players: dict[str, PlayerRecord] = {}
    staff: dict[tuple[str, str], StaffRecord] = {}
    for member in members:
        lane = _lane(member.role)
        if lane:
            players.setdefault(
                member.player,
                PlayerRecord(
                    id=member.player,
                    summoner_name=member.display_name,
                    first_name=member.real_name,
                    last_name="",
                    role=lane,
                    country=member.country,
                ),
            )
        elif member.role not in NON_STAFF_ROLES:
            staff.setdefault(
                (member.player, member.role),
                StaffRecord(
                    name=member.display_name,
                    real_name=member.real_name,
                    role=member.role,
                    country=member.country,
                ),
            )
    tournament = members[0]
    return replace(
        team,
        players=tuple(players.values()),
        staff=tuple(staff.values()),
        leaguepedia_name=tournament.team,
        roster_active=False,
        roster_tournament=tournament.tournament,
        roster_date=tournament.end or tournament.start,
    )
