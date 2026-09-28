-- Local database schema, filled by sync.py and read by the tools.
-- Every statement must be idempotent (CREATE ... IF NOT EXISTS).
-- Datetimes are stored as ISO 8601 UTC strings, so they sort and compare as text.

CREATE TABLE IF NOT EXISTS teams (
    id          TEXT PRIMARY KEY,
    slug        TEXT NOT NULL,
    name        TEXT NOT NULL,
    code        TEXT NOT NULL,
    status      TEXT NOT NULL,  -- 'active' or 'archived'
    home_league TEXT
);
CREATE INDEX IF NOT EXISTS idx_teams_name ON teams (name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS players (
    team_id       TEXT NOT NULL REFERENCES teams (id) ON DELETE CASCADE,
    id            TEXT NOT NULL,
    summoner_name TEXT NOT NULL,
    first_name    TEXT NOT NULL,
    last_name     TEXT NOT NULL,
    role          TEXT NOT NULL,  -- 'top', 'jungle', 'mid', 'bottom', 'support' or 'none'
    PRIMARY KEY (team_id, id)
);

-- Teams are stored by name: the schedule does not expose team ids.
CREATE TABLE IF NOT EXISTS matches (
    id              TEXT PRIMARY KEY,
    start_time      TEXT NOT NULL,
    state           TEXT NOT NULL,  -- 'unstarted', 'inProgress' or 'completed'
    league_slug     TEXT NOT NULL,
    league_name     TEXT NOT NULL,
    block_name      TEXT,
    best_of         INTEGER,        -- NULL when the format is not a best-of
    team1_name      TEXT NOT NULL,
    team1_code      TEXT NOT NULL,
    team1_outcome   TEXT,           -- 'win', 'loss' or NULL until the match is over
    team1_game_wins INTEGER,
    team2_name      TEXT NOT NULL,
    team2_code      TEXT NOT NULL,
    team2_outcome   TEXT,
    team2_game_wins INTEGER
);
CREATE INDEX IF NOT EXISTS idx_matches_team1 ON matches (team1_name COLLATE NOCASE, start_time);
CREATE INDEX IF NOT EXISTS idx_matches_team2 ON matches (team2_name COLLATE NOCASE, start_time);
