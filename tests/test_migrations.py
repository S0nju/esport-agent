import sqlite3
from importlib.resources import files
from pathlib import Path

from esport_agent.db import connect, init_schema
from esport_agent.db.migrations import MIGRATIONS, apply_migrations, schema_version


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]


def test_new_database_gets_every_migration(conn: sqlite3.Connection) -> None:
    assert schema_version(conn) == len(MIGRATIONS)
    assert {"country", "is_substitute"} <= set(columns(conn, "players"))
    assert "leaguepedia_name" in columns(conn, "teams")
    assert columns(conn, "staff") == ["team_id", "name", "real_name", "role", "country"]
    assert {"user_hash", "command", "cost_usd", "error"} <= set(columns(conn, "requests"))


def test_existing_database_is_migrated_and_keeps_its_data(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    old = connect(path)
    old.executescript(files("esport_agent.db").joinpath("schema.sql").read_text("utf-8"))
    old.execute("INSERT INTO teams VALUES ('t1', 'kc', 'Karmine Corp', 'KC', 'active', 'LEC')")
    old.execute("INSERT INTO players VALUES ('t1', 'p1', 'Caliste', 'Caliste', 'H', 'bottom')")
    old.commit()
    assert schema_version(old) == 0
    old.close()

    conn = connect(path)
    init_schema(conn)

    assert schema_version(conn) == len(MIGRATIONS)
    row = conn.execute("SELECT summoner_name, country, is_substitute FROM players").fetchone()
    assert tuple(row) == ("Caliste", None, 0)
    conn.close()


def test_migrations_run_once(conn: sqlite3.Connection) -> None:
    apply_migrations(conn)
    init_schema(conn)

    assert schema_version(conn) == len(MIGRATIONS)
