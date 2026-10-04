"""Schema migrations, applied in order on top of `schema.sql`.

`CREATE TABLE IF NOT EXISTS` never changes a table that already exists, so schema changes
go here. The database stores the number of migrations it has applied in
`PRAGMA user_version`; `apply_migrations` runs the missing ones. A new database goes
through all of them, an existing one only through the new ones, and both end up identical.
Never edit a migration that was released: add a new one.
"""

import sqlite3

MIGRATIONS: tuple[tuple[str, ...], ...] = (
    # 1. Leaguepedia rosters: countries, substitutes, staff, and the team's wiki name.
    (
        "ALTER TABLE teams ADD COLUMN leaguepedia_name TEXT",
        "ALTER TABLE players ADD COLUMN country TEXT",
        "ALTER TABLE players ADD COLUMN is_substitute INTEGER NOT NULL DEFAULT 0",
        """
        CREATE TABLE staff (
            team_id   TEXT NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
            name      TEXT NOT NULL,
            real_name TEXT NOT NULL,
            role      TEXT NOT NULL,
            country   TEXT,
            PRIMARY KEY (team_id, name, role)
        )
        """,
    ),
    # 2. Last known rosters: a team whose current roster is empty on Leaguepedia keeps the
    # one it registered for its last tournament, flagged as no longer active.
    (
        "ALTER TABLE teams ADD COLUMN roster_active INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE teams ADD COLUMN roster_tournament TEXT",
        "ALTER TABLE teams ADD COLUMN roster_date TEXT",
    ),
    # 3. History of the Discord requests, which also backs the /ask quotas. Users are
    # pseudonymized; rows are deleted after the retention period.
    (
        """
        CREATE TABLE requests (
            id            INTEGER PRIMARY KEY,
            created_at    TEXT NOT NULL,
            guild_id      TEXT,
            user_hash     TEXT NOT NULL,
            command       TEXT NOT NULL,
            question      TEXT NOT NULL,
            answer        TEXT,
            tools         TEXT NOT NULL DEFAULT '[]',
            model         TEXT,
            input_tokens  INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cost_usd      REAL NOT NULL DEFAULT 0,
            latency_ms    INTEGER NOT NULL DEFAULT 0,
            error         TEXT
        )
        """,
        "CREATE INDEX idx_requests_created_at ON requests (created_at)",
        "CREATE INDEX idx_requests_user ON requests (user_hash, created_at)",
    ),
)


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Bring the database up to the latest schema, in one transaction."""
    version = schema_version(conn)
    if version >= len(MIGRATIONS):
        return
    with conn:
        for statements in MIGRATIONS[version:]:
            for statement in statements:
                conn.execute(statement)
        # PRAGMA does not accept parameters; the value is an int computed here.
        conn.execute(f"PRAGMA user_version = {len(MIGRATIONS)}")
