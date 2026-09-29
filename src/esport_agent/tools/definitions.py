"""Tool schemas in the Anthropic tool use format."""

from anthropic.types import ToolParam

_TEAM_PROPERTY = {
    "type": "string",
    "description": (
        "Team name, short name or code as the user wrote it (e.g. 'G2 Esports', 'KC'). "
        "Omit it if the user does not name a team."
    ),
}

_LEAGUE_PROPERTY = {
    "type": "string",
    "description": (
        "League or competition the user mentions, as written (e.g. 'LFL', 'LEC', 'Worlds'). "
        "It selects the organization's team playing there ('KC' + 'LFL' is Karmine Corp "
        "Blue) and only keeps that league's matches. Omit it if the user names none."
    ),
}

_LIMIT_PROPERTY = {
    "type": "integer",
    "description": "Number of matches to return (defaults to 5).",
    "minimum": 1,
    "maximum": 20,
}

TOOLS: list[ToolParam] = [
    {
        "name": "get_team_roster",
        "description": (
            "Return the current roster of a League of Legends team: players with their "
            "in-game name, real name and role. A note is added when the source lists several "
            "players for a role (substitutes or inactive players)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"team": _TEAM_PROPERTY, "league": _LEAGUE_PROPERTY},
            "required": [],
        },
    },
    {
        "name": "get_team_next_match",
        "description": (
            "Return the next scheduled match of a League of Legends team, or the match it is "
            "currently playing: date and time, opponent, competition, stage and format. "
            "next_match is null when no match is scheduled."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"team": _TEAM_PROPERTY, "league": _LEAGUE_PROPERTY},
            "required": [],
        },
    },
    {
        "name": "get_team_recent_results",
        "description": (
            "Return the latest results of a League of Legends team, most recent first: date, "
            "opponent, competition, score from the team's point of view and win or loss."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "team": _TEAM_PROPERTY,
                "league": _LEAGUE_PROPERTY,
                "limit": _LIMIT_PROPERTY,
            },
            "required": [],
        },
    },
]
