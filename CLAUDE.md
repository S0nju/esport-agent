# esport-agent

Conversational esports agent (LLM + tool calling, not RAG). MVP: League of Legends,
focused on Karmine Corp, used through a CLI and later a Discord bot.

## Architecture
- `data/`: fetching from external sources (Leaguepedia, lolesports). The only place
  where HTTP calls are made.
- `sync.py`: fills the local SQLite database from `data/`.
- `db/`: database schema and access.
- `tools/`: tools exposed to Claude. They only read the local database, never the APIs.
- `agent.py`: agent loop. `prompts/system.md`: versioned system prompt.
- `cli.py`: local testing of the agent.

## Commands
- `uv run pytest`: tests
- `uv run ruff check . && uv run ruff format .`: lint and formatting
- `uv run mypy src`: type checking
- `uv run python -m esport_agent.sync`: update the database
- `uv run python -m esport_agent.cli`: run the agent locally

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

## Before saying a task is done
Run ruff, mypy and pytest. If any of them fails, fix it before concluding, or clearly
explain what is still broken and why.

## Git
- Never commit directly to `main`. One branch per task:
  `feat/...`, `fix/...`, `chore/...`, `docs/...`.
- Conventional Commits format: `feat: add the get_team_roster tool`.
- Small, coherent commits: one commit = one change that makes sense on its own.
- Never commit `.env`, the SQLite database or generated files.
- Do not commit, push or open a PR without my approval.
- Never `git push --force` or rewrite history on `main`.
- Merge into `main` through pull requests only, with CI green.
