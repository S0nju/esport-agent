"""Tool schemas in the Anthropic tool use format."""

from anthropic.types import ToolParam

_TEAM_PROPERTY = {
    "type": "string",
    "description": (
        "Full team name (e.g. 'G2 Esports'). Omit it if the user does not name a team."
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
            "Return the current roster of a League of Legends team (players and roles)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"team": _TEAM_PROPERTY},
            "required": [],
        },
    },
    {
        "name": "get_team_next_match",
        "description": (
            "Return the next scheduled match of a League of Legends team "
            "(date, opponent, competition)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"team": _TEAM_PROPERTY},
            "required": [],
        },
    },
    {
        "name": "get_team_recent_results",
        "description": (
            "Return the results of the latest matches of a League of Legends team "
            "(opponent, score, winner)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"team": _TEAM_PROPERTY, "limit": _LIMIT_PROPERTY},
            "required": [],
        },
    },
]
