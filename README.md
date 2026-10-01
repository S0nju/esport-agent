# esport-agent

[![CI](https://github.com/S0nju/esport-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/S0nju/esport-agent/actions/workflows/ci.yml)
![Python 3.14](https://img.shields.io/badge/python-3.14-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

A conversational agent that answers natural-language questions about League of Legends
esports: a team's roster, its next match, its latest results. It is built on Claude with
tool calling over a local database kept up to date from the lolesports API, and is meant to
run as a Discord bot. Today it runs as a command-line app.

## Example

```text
> Roster de Vitality ?
Roster de **Team Vitality** :
- Top : Naak Nako
- Jungle : Lyncas
- Mid : FIESTA
- Mid : Humanoid
- Bot : Carzzy
- Support : Fleshy

Le roster peut inclure des remplaçants ou des joueurs inactifs.
[claude-haiku-4-5-20251001 · 2 calls · 4,039 in / 139 out tokens · ≈ $0.0047]

> What were KC's last 3 results?
Last results of **Karmine Corp**:
- 19/09: ❌ 0-3 vs Movistar KOI (LEC, Playoffs)
- 06/09: ❌ 1-3 vs G2 Esports (LEC, Playoffs)
- 05/09: ✅ 3-1 vs GIANTX (LEC, Playoffs)
[claude-haiku-4-5-20251001 · 2 calls · 4,046 in / 177 out tokens · ≈ $0.0049]
```

Real output, with data from September 2026. The line in brackets is the usage report
printed after each answer.

## Features

- **Three questions**: current roster, next match (or the match being played), latest
  results with scores from the team's point of view. Rosters leave out members without a
  role and say when the source lists several players for a role.
- **Flexible team names**: full name, short name or code ("Karmine Corp", "KC"). A short
  name shared by several teams resolves to the one playing in the preferred leagues
  ("Vitality" is Team Vitality, not Vitality.Bee); if that does not settle it, the agent
  asks which team you mean.
- **League-aware**: naming a league picks the organization's team that plays there and keeps
  only that league's matches ("KC LFL" is Karmine Corp Blue, "G2 at Worlds" shows G2's
  Worlds matches).
- **Multilingual**: answers in the language of the question, with match times in the
  configured time zone and relative dates ("tonight", "in 3 days").
- **Grounded**: every fact comes from the database; missing data (coaches, nationalities)
  is reported as missing instead of guessed.
- **Cost-aware**: each answer shows the model used, the tokens and an estimated cost (about
  $0.005 per question with Claude Haiku 4.5).

## Architecture

```text
 lolesports API ──► data/lolesports.py ──► sync.py ──► SQLite ◄── tools/ ◄──► Claude
 (unofficial)       typed client,          one                    roster,     (agent.py)
                    validated responses    transaction            next match,     │
                                                                  results         ▼
                                                                          CLI / Discord bot
```

| Module | Role |
|---|---|
| `data/` | The only place where HTTP calls are made. `lolesports.py` is a typed client whose responses are validated with pydantic, so a change in the unofficial API fails loudly. |
| `sync.py` | Fetches every team with its roster and the schedule of the configured leagues, then writes everything in a single transaction. |
| `db/` | SQLite schema, source-agnostic records and queries (team search, next match, results). |
| `tools/` | Tool schemas for Claude and the functions behind them: team name resolution, JSON answers, local times. They only read the database. |
| `agent.py` | The tool-use loop: calls Claude, runs the requested tools, returns the answer with its usage. |
| `prompts/system.md` | The system prompt, versioned separately from the code. |
| `cli.py` | Interactive command line, with a `--tools` mode that calls the tools without Claude. |

## Design choices

- **Tool calling rather than RAG.** The questions are about structured, changing data
  (rosters, schedules, scores). Letting the model call typed functions over a database gives
  exact answers and lets it combine them; retrieving text chunks would not.
- **A local database instead of live API calls.** The tools never call external APIs: a
  sync script fills SQLite ahead of time. Answers are fast and do not depend on the
  availability or rate limits of the sources, and every source goes through the same
  schema, which is independent of where the data comes from.
- **Defensive handling of an unofficial API.** Responses are validated against pydantic
  models, a failed call leaves the database untouched, duplicated entries are skipped, and
  upcoming matches that disappear from the schedule (cancelled or moved) are removed while
  past results are kept.
- **Team name resolution in the tools, not in the prompt.** Exact name, code or slug first,
  ranked to prefer active teams in a league; then partial names, settled by the preferred
  leagues; and an explicit list of candidates when it stays ambiguous, so the model asks
  instead of guessing.
- **Nothing team-specific in the code.** The default team, preferred leagues, synced leagues
  and time zone are settings: Karmine Corp is only the default value.
- **Controlled costs.** Claude Haiku 4.5 by default, usage and cost reported per answer, and
  for the Discord bot, slash commands that answer common questions without calling the model
  at all.

## Getting started

Requirements: [uv](https://docs.astral.sh/uv/), an
[Anthropic API key](https://console.anthropic.com/) for the agent (not needed for the sync
or the `--tools` mode).

```bash
uv sync
cp .env.example .env          # then set ANTHROPIC_API_KEY

uv run python -m esport_agent.sync           # fill the local database
uv run python -m esport_agent.cli            # ask the agent questions
uv run python -m esport_agent.cli --tools    # call the tools directly, without Claude
```

In `--tools` mode, type `help` for the list of tools, then for example
`get_team_recent_results team="Karmine Corp" limit=3`.

### Configuration

Settings are read from environment variables or `.env` (see `.env.example`).

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | | Required by the agent. |
| `LOLESPORTS_API_KEY` | public key in `.env.example` | Required by the sync. |
| `CLAUDE_MODEL` | `claude-haiku-4-5-20251001` | Model used by the agent. |
| `DEFAULT_TEAM` | `Karmine Corp` | Team used when a question names none. |
| `LOLESPORTS_LEAGUES` | `["lec", "lfl", "worlds", "msi", "first_stand"]` | Leagues whose schedule is synced. |
| `PREFERRED_LEAGUES` | `["lec"]` | Leagues used to pick a team from a short name. |
| `TIMEZONE` | `Europe/Paris` | Time zone of match times and of today's date. |
| `SQLITE_PATH` | `esport_agent.db` | Local database file. |
| `LOG_LEVEL` | `INFO` (sync), `WARNING` (CLI) | `DEBUG` also shows every HTTP request; `INFO` in the CLI shows the tool calls and their cost. |
| `LOG_FILE` | | Also write logs to this file, rotated at 5 MB (3 old files kept). |

## Development

```bash
uv run ruff check . && uv run ruff format .   # lint and formatting
uv run mypy src tests                         # strict type checking
uv run pytest                                 # tests, with HTTP and Claude calls mocked
uv run pre-commit install                     # run ruff on every commit
```

The CI runs the same checks on every pull request. `main` is protected: changes go through
pull requests with a green CI, and dependencies are kept up to date by Dependabot.

## Roadmap

- **Discord bot**: free slash commands (`/roster`, `/next`, `/results`) that call the tools
  directly, and an `/ask` command for free-text questions with per-user quotas.
- **Leaguepedia**: player nationalities, coaches, substitutes, LFL Division 2 and the full
  match history.

## Disclaimer

This project is not affiliated with or endorsed by Riot Games. It relies on the unofficial
lolesports API, which may change without notice. League of Legends is a trademark of Riot
Games, Inc.

## License

[MIT](LICENSE). Built by Kelian Ninet ([@S0nju](https://github.com/S0nju)).
