import sqlite3

import pytest

from esport_agent.config import Settings
from esport_agent.sync import run_sync


def test_run_sync_not_implemented(conn: sqlite3.Connection, settings: Settings) -> None:
    with pytest.raises(NotImplementedError):
        run_sync(conn, settings, settings.default_team)
