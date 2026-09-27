import sqlite3
from pathlib import Path

from esport_agent.db import connect, init_schema


def test_connect_uses_row_factory_and_foreign_keys(tmp_path: Path) -> None:
    conn = connect(tmp_path / "test.db")
    try:
        assert conn.row_factory is sqlite3.Row
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_init_schema_is_idempotent(conn: sqlite3.Connection) -> None:
    init_schema(conn)
    init_schema(conn)
