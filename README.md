# esport-agent

Conversational esports agent that answers natural-language questions about League of Legends:
a team's roster, its next match, its recent results. It uses Claude with tool calling (no RAG):
Claude picks a tool, the tool reads a local SQLite database, and Claude writes the answer.

The MVP focuses on Karmine Corp (the default team), but the team is always a parameter.
The agent will be exposed through a Discord bot; for now, only a test CLI exists.

## Stack

- Python 3.14, [uv](https://docs.astral.sh/uv/)
- [anthropic](https://github.com/anthropics/anthropic-sdk-python): Claude API calls
- [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/): configuration
- [mwrogue](https://github.com/RheingoldRiver/mwrogue): Leaguepedia (Cargo API)
- [httpx](https://www.python-httpx.org/): unofficial lolesports API (schedule)
- SQLite (standard `sqlite3` module): local database
- ruff, mypy (strict), pytest, pre-commit

## Data

Leaguepedia has very low rate limits, so the tools never call it. The `esport_agent.sync`
script fetches the data (Leaguepedia + lolesports) and fills the SQLite database; the tools
only read that database.

## Running locally

```bash
uv sync
cp .env.example .env              # then set ANTHROPIC_API_KEY
uv run pre-commit install

uv run python -m esport_agent.sync   # update the local database
uv run python -m esport_agent.cli    # start the agent in interactive mode
```

## Quality checks

```bash
uv run ruff check . && uv run ruff format .
uv run mypy src
uv run pytest
```
