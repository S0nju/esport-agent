# esport-agent

Conversational esports agent that answers natural-language questions about League of Legends:
a team's roster, its next match, its recent results. It uses Claude with tool calling (no RAG):
Claude picks a tool, the tool reads a local SQLite database, and Claude writes the answer.

The MVP focuses on Karmine Corp (the default team), but the team is always a parameter.
The agent will be exposed through a Discord bot; for now, only a test CLI exists.

## What the agent can answer

| Tool | Example question |
|---|---|
| `get_team_roster` | "Who plays for KC?" |
| `get_team_next_match` | "When does G2 play next?" |
| `get_team_recent_results` | "How did Karmine Corp do in its last 3 matches?" |

Teams can be named by full name, short name or code ("Karmine Corp", "KC"). When a short
name matches several teams, the one playing in `PREFERRED_LEAGUES` (the LEC by default) is
picked: "Vitality" gives Team Vitality, not Vitality.Bee. If no preferred league settles it,
the agent asks which team you mean. Match times are shown in the `TIMEZONE` setting
(Europe/Paris by default).

The agent answers in the language of the question, casually (it uses "tu" in French),
concisely and only from the synced data.

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
uv run python -m esport_agent.cli --tools   # call the tools directly, without Claude (free)
```

After each answer, the CLI shows the model used, the number of Claude calls, the tokens
and an estimated cost, e.g. `[claude-haiku-4-5-20251001 · 2 calls · 3,412 in / 187 out
tokens · ≈ $0.0043]`. The Usage page of the Anthropic console remains the source of truth.

In `--tools` mode, type `help` for the list of tools, then for example
`get_team_recent_results team="Karmine Corp" limit=3`. No Anthropic key is needed: it is the
free way to check the data and the team name resolution.

## Quality checks

```bash
uv run ruff check . && uv run ruff format .
uv run mypy src tests
uv run pytest
```
