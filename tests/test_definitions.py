from esport_agent.tools.definitions import TOOLS


def test_tool_names() -> None:
    assert [tool["name"] for tool in TOOLS] == [
        "get_team_roster",
        "get_team_next_match",
        "get_team_recent_results",
    ]


def test_team_is_optional_everywhere() -> None:
    for tool in TOOLS:
        schema = tool["input_schema"]
        assert isinstance(schema, dict)
        properties, required = schema["properties"], schema["required"]
        assert isinstance(properties, dict)
        assert isinstance(required, list)
        assert "team" in properties
        assert "team" not in required
