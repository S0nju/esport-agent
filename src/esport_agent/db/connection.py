"""SQLite connection and schema initialization."""

import sqlite3
from importlib.resources import files
from pathlib import Path

from esport_agent.db.migrations import apply_migrations


def connect(path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection whose rows are accessible by column name."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the tables if they do not exist yet, then apply the pending migrations."""
    schema = files("esport_agent.db").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema)
    apply_migrations(conn)
