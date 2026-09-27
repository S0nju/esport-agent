"""Update the local SQLite database from Leaguepedia and lolesports.

Usage : `uv run python -m esport_agent.sync`
"""

import logging
import sqlite3
from contextlib import closing

from esport_agent.config import Settings, get_settings
from esport_agent.db import connect, init_schema

logger = logging.getLogger(__name__)


def run_sync(conn: sqlite3.Connection, settings: Settings, team: str) -> None:
    """Fetch `team`'s data from the external sources and write it to the database."""
    raise NotImplementedError


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()
    with closing(connect(settings.sqlite_path)) as conn:
        init_schema(conn)
        logger.info("Syncing %s into %s", settings.default_team, settings.sqlite_path)
        run_sync(conn, settings, settings.default_team)
        conn.commit()


if __name__ == "__main__":
    main()
