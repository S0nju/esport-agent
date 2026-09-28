# esport-agent

Conversational esports agent (LLM + tool calling, not RAG). MVP: League of Legends,
focused on Karmine Corp, used through a CLI and later a Discord bot.

## Architecture
- `data/`: fetching from external sources. The only place where HTTP calls are made.
  - `lolesports.py`: unofficial lolesports API (leagues, teams with rosters, schedules).
    Feeds the whole MVP.
  - `leaguepedia.py`: stub. Planned after the MVP for player countries, coaches, staff,
    LFL Division 2 and full history; needs bot credentials (anonymous rate limits are too low).
- `sync.py`: fills the local SQLite database from `data/`, in one transaction.
- `db/`: database schema (`schema.sql`), source-agnostic records (`records.py`) and
  reads/writes (`repository.py`). Datetimes are stored as ISO 8601 UTC strings.
- `tools/`: tools exposed to Claude. They only read the local database, never the APIs.
  `definitions.py` holds the schemas, `handlers.py` the tool functions and the dispatch.
  Tools accept a team name, short name or code, return JSON (an `error` with candidates
  when the team is unknown or ambiguous) and give times in the configured `TIMEZONE`.
  A short name matching several teams resolves to the one in `PREFERRED_LEAGUES`.
- `agent.py`: agent loop; sends today's date and time to Claude after the static system
  prompt. `prompts/system.md`: versioned system prompt.
- `usage.py`: token usage and estimated cost of the Claude calls made for one question.
- `cli.py`: local testing of the agent.

## Commands
- `uv run pytest`: tests
- `uv run ruff check . && uv run ruff format .`: lint and formatting
- `uv run mypy src tests`: type checking
- `uv run python -m esport_agent.sync`: update the database
- `uv run python -m esport_agent.cli`: run the agent locally
- `uv run python -m esport_agent.cli --tools`: call the tools directly, without Claude (free)

## Code rules
- Write everything in English: code, comments, docstrings, log messages, commit messages,
  README, CLAUDE.md and other docs.
- Nothing team-specific in the code: the team is always a parameter.
- One tool = one pure, typed function, tested on its own.
- Any new external source goes through a new module in `data/`.
- Full typing, mypy strict must pass. No `Any` without a reason written in a comment.
- Tests: HTTP and Anthropic calls are always mocked. All new code comes with tests.
- No `print` outside `cli.py`, use `logging`.
- No secrets in the code: everything goes through `config.py` and `.env`.
- Do not add a dependency without asking me first.
- Prefer small, focused changes. Do not refactor anything unrelated to the task.
- Keep README.md and CLAUDE.md up to date when a change or a decision affects them.

## Before saying a task is done
Run ruff, mypy and pytest. If any of them fails, fix it before concluding, or clearly
explain what is still broken and why.
Then review the full diff of the branch (correctness, edge cases, tests, typing, the rules
above) and report what the review found before handing over the commit commands.

## Git
- Never commit directly to `main`. One branch per task:
  `feat/...`, `fix/...`, `chore/...`, `docs/...`.
- Conventional Commits format: `feat: add the get_team_roster tool`.
- Small, coherent commits: one commit = one change that makes sense on its own.
- Never commit `.env`, the SQLite database or generated files.
- Do not commit, push or open a PR without my approval: I run git commit, push and merge
  myself. Prepare the changes and give me the commands.
- Never `git push --force` or rewrite history on `main`.
- Merge into `main` through pull requests only, with CI green.
