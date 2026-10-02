"""Replies of the free slash commands: run a tool, then format its result for Discord.

No Claude call here: the formats are the ones the agent uses (`prompts/system.md`), built
in code, in the user's language. Dates are Discord timestamps (`<t:...>`), which each
user's client shows in their own language and time zone. Every name that comes from the
user or the sources is escaped, so that a "_" or a "*" is not read as Markdown.
"""

import re
import sqlite3
from collections import Counter
from datetime import UTC, date, datetime, time
from typing import TypeIs

from esport_agent.bot.translations import Lang, text
from esport_agent.config import Settings
from esport_agent.tools.handlers import (
    ROLE_LABELS,
    MatchInfo,
    NextMatchResponse,
    RecentResultsResponse,
    RosterResponse,
    ToolError,
    get_team_next_match,
    get_team_recent_results,
    get_team_roster,
)

MAX_CANDIDATES = 10
"""Candidates listed when a team name is ambiguous, to keep the message short."""
OUTCOMES = {"win": "✅", "loss": "❌"}
MARKDOWN_CHARACTERS = re.compile(r"([\\*_`~|>\[\]])")
"""Characters Discord reads as formatting (bold, italics, code, spoilers, quotes, links)."""


def roster_reply(
    conn: sqlite3.Connection,
    settings: Settings,
    lang: Lang,
    team: str | None,
    league: str | None = None,
    staff: bool = False,
) -> str:
    query = team or settings.default_team
    result = get_team_roster(conn, query, settings.preferred_leagues, league)
    if _is_error(result):
        return error_message(result, lang, query, league)
    return format_roster(result, lang, staff=staff)


def next_match_reply(
    conn: sqlite3.Connection,
    settings: Settings,
    lang: Lang,
    team: str | None,
    league: str | None = None,
    now: datetime | None = None,
) -> str:
    query = team or settings.default_team
    result = get_team_next_match(
        conn,
        query,
        now or datetime.now(UTC),
        settings.tzinfo,
        settings.preferred_leagues,
        league,
    )
    if _is_error(result):
        return error_message(result, lang, query, league)
    return format_next_match(result, lang)


def results_reply(
    conn: sqlite3.Connection,
    settings: Settings,
    lang: Lang,
    team: str | None,
    league: str | None = None,
    limit: int = 5,
) -> str:
    query = team or settings.default_team
    result = get_team_recent_results(
        conn, query, limit, settings.tzinfo, settings.preferred_leagues, league
    )
    if _is_error(result):
        return error_message(result, lang, query, league)
    return format_results(result, lang)


def format_roster(result: RosterResponse, lang: Lang, *, staff: bool = False) -> str:
    team = _md(result["team"]["name"])
    if not result["players"]:
        return text("no_players", lang, team=team)
    last_known = result.get("last_known_roster")
    if last_known:
        ended = last_known["ended"]
        title = text(
            "last_known_title",
            lang,
            team=team,
            tournament=_md(last_known["tournament"]),
            ended=text("ended", lang, date=_timestamp(date.fromisoformat(ended), "D"))
            if ended
            else "",
        )
    else:
        title = text("roster_title", lang, team=team)
    lines = [title]
    for player in result["players"]:
        name = _md(player["summoner_name"])
        if player["substitute"]:
            name += text("substitute", lang)
        lines.append(text("line", lang, label=player["role"], value=name))
    if staff and result["staff"]:
        lines.append(text("staff_title", lang))
        lines.extend(
            text("line", lang, label=_md(member["role"]), value=_md(member["name"]))
            for member in result["staff"]
        )
    lines.extend(f"-# {note}" for note in _roster_notes(result, lang))
    return "\n".join(lines)


def format_next_match(result: NextMatchResponse, lang: Lang) -> str:
    team = _md(result["team"]["name"])
    match = result["next_match"]
    if match is None:
        return text("no_next_match", lang, team=team)
    opponent = _md(match["opponent"])
    context = _context(match, with_format=True)
    if match["state"] == "inProgress":
        return text("live_match", lang, team=team, opponent=opponent, context=context)
    start = datetime.fromisoformat(match["start_time"])
    return text(
        "next_match",
        lang,
        team=team,
        opponent=opponent,
        when=_timestamp(start, "F"),
        relative=_timestamp(start, "R"),
        context=context,
    )


def format_results(result: RecentResultsResponse, lang: Lang) -> str:
    team = _md(result["team"]["name"])
    if not result["results"]:
        return text("no_results", lang, team=team)
    lines = [text("results_title", lang, team=team)]
    for match in result["results"]:
        lines.append(
            text(
                "result_line",
                lang,
                date=_timestamp(datetime.fromisoformat(match["start_time"]), "d"),
                outcome=OUTCOMES.get(match["result"] or "", "❔"),
                score=match["score"],
                opponent=_md(match["opponent"]),
                context=_context(match),
            )
        )
    return "\n".join(lines)


def error_message(error: ToolError, lang: Lang, query: str, league: str | None) -> str:
    """Word a tool error in `lang`, from its code rather than its English text."""
    values = {"query": _md(query), "league": _md(league or "")}
    message = text(error["code"], lang, **values)
    if error["code"] != "ambiguous_team":
        return message
    candidates = error["candidates"]
    lines = [message, *(f"- {_md(c)}" for c in candidates[:MAX_CANDIDATES])]
    if len(candidates) > MAX_CANDIDATES:
        lines.append("- …")
    return "\n".join(lines)


def _is_error(result: object) -> TypeIs[ToolError]:
    return isinstance(result, dict) and "error" in result


def _roster_notes(result: RosterResponse, lang: Lang) -> list[str]:
    """The roster warnings of the tool, rebuilt in `lang` from the players."""
    starters = Counter(p["role"] for p in result["players"] if not p["substitute"])
    notes = []
    if any(count > 1 for count in starters.values()):
        notes.append(text("several_starters", lang))
    missing = [label for label in ROLE_LABELS.values() if label not in starters]
    if missing:
        notes.append(text("missing_starters", lang, roles=", ".join(missing)))
    return notes


def _context(match: MatchInfo, *, with_format: bool = False) -> str:
    """ "LEC, Playoffs" (and ", Bo5" for an upcoming match), without the missing parts."""
    parts = [match["league"], match["stage"]]
    if with_format and match["best_of"]:
        parts.append(f"Bo{match['best_of']}")
    return ", ".join(_md(part) for part in parts if part)


def _timestamp(moment: datetime | date, style: str) -> str:
    """A Discord timestamp: each client shows it in its own language and time zone."""
    if not isinstance(moment, datetime):
        # A date alone (end of a tournament): noon UTC is that same day from UTC-11 to +11.
        moment = datetime.combine(moment, time(12), UTC)
    return f"<t:{int(moment.timestamp())}:{style}>"


def _md(value: str) -> str:
    """Escape Discord's formatting characters: "Bench_Guy" stays "Bench_Guy" on screen."""
    return MARKDOWN_CHARACTERS.sub(r"\\\1", value)
