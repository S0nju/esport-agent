"""Builders of database records for tests."""

from datetime import UTC, datetime

from esport_agent.db import MatchRecord, MatchSide, PlayerRecord, TeamRecord


def make_player(player_id: str, role: str = "mid") -> PlayerRecord:
    return PlayerRecord(
        id=player_id, summoner_name=player_id, first_name="First", last_name="Last", role=role
    )


def make_team(
    team_id: str = "t1",
    players: tuple[PlayerRecord, ...] = (),
    *,
    name: str | None = None,
    code: str | None = None,
    status: str = "active",
    home_league: str | None = "LEC",
) -> TeamRecord:
    return TeamRecord(
        id=team_id,
        slug=f"team-{team_id}",
        name=name or f"Team {team_id}",
        code=code or team_id.upper(),
        status=status,
        home_league=home_league,
        players=players,
    )


def make_match(
    match_id: str = "m1",
    state: str = "unstarted",
    *,
    start_time: datetime = datetime(2026, 9, 19, 15, tzinfo=UTC),
    team1: str = "Team A",
    team2: str = "Team B",
    score: tuple[int, int] | None = None,
) -> MatchRecord:
    """Build a match; with `score`, it is completed and won by the team with more games."""
    outcomes: tuple[str | None, str | None] = (None, None)
    if score is not None:
        state = "completed"
        outcomes = ("win", "loss") if score[0] > score[1] else ("loss", "win")
    return MatchRecord(
        id=match_id,
        start_time=start_time,
        state=state,
        league_slug="lec",
        league_name="LEC",
        block_name="Playoffs",
        best_of=5,
        team1=MatchSide(
            name=team1,
            code=team1[:3].upper(),
            outcome=outcomes[0],
            game_wins=score[0] if score else None,
        ),
        team2=MatchSide(
            name=team2,
            code=team2[:3].upper(),
            outcome=outcomes[1],
            game_wins=score[1] if score else None,
        ),
    )
