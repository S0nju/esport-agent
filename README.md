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
- [httpx](https://www.python-httpx.org/): unofficial lolesports API
- [mwrogue](https://github.com/RheingoldRiver/mwrogue): Leaguepedia (Cargo API), planned
- SQLite (standard `sqlite3` module): local database
- ruff, mypy (strict), pytest, pre-commit

## Data

The tools never call external APIs: they only read a local SQLite database, which the
`esport_agent.sync` script fills.

- **lolesports** (unofficial API, current source): every team with its roster, and the
  schedule and results of the leagues listed in `LOLESPORTS_LEAGUES` (LEC, LFL, Worlds, MSI
  and First Stand by default). Its history starts in 2024.
- **Leaguepedia** (planned): player countries, coaches and staff, LFL Division 2 and the full
  history. Its anonymous rate limits are too low for a sync, so it will need bot credentials
  (`LEAGUEPEDIA_BOT_USERNAME` / `LEAGUEPEDIA_BOT_PASSWORD`).

Each sync replaces teams and rosters, and adds or updates matches: past results are kept
across syncs, and cancelled upcoming matches are removed.

## Running locally

```bash
uv sync
cp .env.example .env              # then set ANTHROPIC_API_KEY
uv run pre-commit install

uv run python -m esport_agent.sync   # update the local database (needs LOLESPORTS_API_KEY)
uv run python -m esport_agent.cli    # start the agent (needs ANTHROPIC_API_KEY and a synced database)
```

## Quality checks

```bash
uv run ruff check . && uv run ruff format .
uv run mypy src tests
uv run pytest
```
