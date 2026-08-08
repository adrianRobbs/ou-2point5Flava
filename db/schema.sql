-- Over/Under 2.5 raw extraction schema.
-- One-shot bootstrap: run once against a fresh Neon database.
-- No migration framework for v1 — revisit with Alembic if the schema
-- needs to evolve under live data.

CREATE TABLE IF NOT EXISTS competitions (
    competition_id TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    country        TEXT,
    type           TEXT
);

-- `is_tracked` is the operational scope for discover/refresh/backfill-coverage
-- (see orchestration/catalog.py) — deliberately separate from `tier`, which
-- is a frozen backtest classification (export/match_csv.py's
-- TIER_BY_COMPETITION) that only ever applies to already-studied
-- competitions, not anything newly added here. ADD COLUMN IF NOT EXISTS so
-- re-running this file against the existing production table is safe, per
-- this file's own "no migration framework for v1" convention.
ALTER TABLE competitions ADD COLUMN IF NOT EXISTS is_tracked BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE competitions ADD COLUMN IF NOT EXISTS tier INTEGER;

-- The provider gives no start/end dates for a season — only a display name
-- ("Premier League 24/25"), a short year code ("24/25"), and start/end years.
CREATE TABLE IF NOT EXISTS seasons (
    season_id      TEXT PRIMARY KEY,
    competition_id TEXT NOT NULL REFERENCES competitions (competition_id),
    name           TEXT NOT NULL,
    year           TEXT NOT NULL,
    start_year     INTEGER,
    end_year       INTEGER,
    is_current     BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS teams (
    team_id TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    country TEXT
);

CREATE TABLE IF NOT EXISTS referees (
    referee_id        TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    country           TEXT,
    career_games      INTEGER,
    career_yellow     INTEGER,
    career_red        INTEGER,
    career_yellow_red INTEGER
);

-- group_label is '' (not NULL) for linear leagues so it can sit in a
-- composite primary key; knockout-only cups simply have no rows here.
CREATE TABLE IF NOT EXISTS standings (
    season_id        TEXT NOT NULL REFERENCES seasons (season_id),
    team_id          TEXT NOT NULL REFERENCES teams (team_id),
    group_label      TEXT NOT NULL DEFAULT '',
    position         INTEGER,
    total_teams      INTEGER,
    matches_played   INTEGER,
    wins             INTEGER,
    draws            INTEGER,
    losses           INTEGER,
    goals_for        INTEGER,
    goals_against    INTEGER,
    goal_difference  INTEGER,
    points           INTEGER,
    PRIMARY KEY (season_id, team_id, group_label)
);

CREATE TABLE IF NOT EXISTS matches (
    match_id       TEXT PRIMARY KEY,
    competition_id TEXT NOT NULL REFERENCES competitions (competition_id),
    season_id      TEXT NOT NULL REFERENCES seasons (season_id),
    matchday       INTEGER,
    stage_name     TEXT,
    group_label    TEXT,
    kickoff_utc    TIMESTAMPTZ NOT NULL,
    home_team_id   TEXT NOT NULL REFERENCES teams (team_id),
    away_team_id   TEXT NOT NULL REFERENCES teams (team_id),
    venue_name     TEXT,
    venue_city     TEXT,
    referee_id     TEXT REFERENCES referees (referee_id),
    home_goals_ft  INTEGER,
    away_goals_ft  INTEGER,
    home_goals_ht  INTEGER,
    away_goals_ht  INTEGER,
    status         TEXT NOT NULL,
    odds_available BOOLEAN NOT NULL DEFAULT FALSE,
    xg_available   BOOLEAN NOT NULL DEFAULT FALSE,
    total_goals_ft INTEGER GENERATED ALWAYS AS (home_goals_ft + away_goals_ft) STORED,
    target_over_2_5 BOOLEAN GENERATED ALWAYS AS (
        CASE WHEN home_goals_ft IS NULL OR away_goals_ft IS NULL THEN NULL
             ELSE (home_goals_ft + away_goals_ft) >= 3
        END
    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_matches_competition_season ON matches (competition_id, season_id);
CREATE INDEX IF NOT EXISTS idx_matches_home_team ON matches (home_team_id, kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_matches_away_team ON matches (away_team_id, kickoff_utc);

-- Post-match box score only. Never join back to the same match_id as a
-- predictive feature — see Columns-Discovery.md / Phase 2 notes.
CREATE TABLE IF NOT EXISTS match_team_stats (
    match_id        TEXT NOT NULL REFERENCES matches (match_id),
    team_id         TEXT NOT NULL REFERENCES teams (team_id),
    side            TEXT NOT NULL CHECK (side IN ('home', 'away')),
    shots_total     INTEGER,
    shots_on_target INTEGER,
    possession_pct  NUMERIC(5, 2),
    corners         INTEGER,
    xg              NUMERIC(5, 2),
    PRIMARY KEY (match_id, team_id)
);

-- Long/tall format: markets vary in shape (2-way, 3-way, handicap lines),
-- so one row per (bookmaker, market, selection) avoids a sparse wide table.
-- Wide, not long: one row per (match, bookmaker), opposite sides of the same
-- line as columns side by side. A long/tall table made "what's the under 2.5
-- price for the match whose over 2.5 I'm looking at" require a self-join —
-- exactly backwards for a training-data table where both sides of a line are
-- almost always used together. total_goals lines are a fixed, empirically
-- observed set (0.5 through 9.5); an unseen line beyond that is dropped
-- rather than silently misfiled, since there's no column for it.
CREATE TABLE IF NOT EXISTS match_odds (
    match_id   TEXT NOT NULL REFERENCES matches (match_id),
    bookmaker  TEXT NOT NULL,

    home_odds_open   NUMERIC(8, 3),
    home_odds_close  NUMERIC(8, 3),
    draw_odds_open   NUMERIC(8, 3),
    draw_odds_close  NUMERIC(8, 3),
    away_odds_open   NUMERIC(8, 3),
    away_odds_close  NUMERIC(8, 3),

    btts_yes_odds_open  NUMERIC(8, 3),
    btts_yes_odds_close NUMERIC(8, 3),
    btts_no_odds_open   NUMERIC(8, 3),
    btts_no_odds_close  NUMERIC(8, 3),

    goals_0_5_over_open   NUMERIC(8, 3),
    goals_0_5_over_close  NUMERIC(8, 3),
    goals_0_5_under_open  NUMERIC(8, 3),
    goals_0_5_under_close NUMERIC(8, 3),
    goals_1_5_over_open   NUMERIC(8, 3),
    goals_1_5_over_close  NUMERIC(8, 3),
    goals_1_5_under_open  NUMERIC(8, 3),
    goals_1_5_under_close NUMERIC(8, 3),
    goals_2_5_over_open   NUMERIC(8, 3),
    goals_2_5_over_close  NUMERIC(8, 3),
    goals_2_5_under_open  NUMERIC(8, 3),
    goals_2_5_under_close NUMERIC(8, 3),
    goals_3_5_over_open   NUMERIC(8, 3),
    goals_3_5_over_close  NUMERIC(8, 3),
    goals_3_5_under_open  NUMERIC(8, 3),
    goals_3_5_under_close NUMERIC(8, 3),
    goals_4_5_over_open   NUMERIC(8, 3),
    goals_4_5_over_close  NUMERIC(8, 3),
    goals_4_5_under_open  NUMERIC(8, 3),
    goals_4_5_under_close NUMERIC(8, 3),
    goals_5_5_over_open   NUMERIC(8, 3),
    goals_5_5_over_close  NUMERIC(8, 3),
    goals_5_5_under_open  NUMERIC(8, 3),
    goals_5_5_under_close NUMERIC(8, 3),
    goals_6_5_over_open   NUMERIC(8, 3),
    goals_6_5_over_close  NUMERIC(8, 3),
    goals_6_5_under_open  NUMERIC(8, 3),
    goals_6_5_under_close NUMERIC(8, 3),
    goals_7_5_over_open   NUMERIC(8, 3),
    goals_7_5_over_close  NUMERIC(8, 3),
    goals_7_5_under_open  NUMERIC(8, 3),
    goals_7_5_under_close NUMERIC(8, 3),
    goals_8_5_over_open   NUMERIC(8, 3),
    goals_8_5_over_close  NUMERIC(8, 3),
    goals_8_5_under_open  NUMERIC(8, 3),
    goals_8_5_under_close NUMERIC(8, 3),
    goals_9_5_over_open   NUMERIC(8, 3),
    goals_9_5_over_close  NUMERIC(8, 3),
    goals_9_5_under_open  NUMERIC(8, 3),
    goals_9_5_under_close NUMERIC(8, 3),

    PRIMARY KEY (match_id, bookmaker)
);

CREATE TABLE IF NOT EXISTS match_lineups (
    match_id   TEXT NOT NULL REFERENCES matches (match_id),
    team_id    TEXT NOT NULL REFERENCES teams (team_id),
    side       TEXT NOT NULL CHECK (side IN ('home', 'away')),
    confirmed  BOOLEAN NOT NULL DEFAULT FALSE,
    formation  TEXT,
    PRIMARY KEY (match_id, team_id)
);

CREATE TABLE IF NOT EXISTS match_lineup_players (
    match_id   TEXT NOT NULL REFERENCES matches (match_id),
    team_id    TEXT NOT NULL REFERENCES teams (team_id),
    player_id  TEXT NOT NULL,
    is_starter BOOLEAN NOT NULL,
    PRIMARY KEY (match_id, team_id, player_id)
);

-- Current-state only (see Phase 4 limitation note) — populated meaningfully
-- once the pipeline runs in incremental/live mode, not during the historical backfill.
CREATE TABLE IF NOT EXISTS team_injuries_suspensions (
    team_id    TEXT NOT NULL REFERENCES teams (team_id),
    player_id  TEXT NOT NULL,
    type       TEXT NOT NULL,
    status     TEXT,
    as_of_date DATE NOT NULL,
    PRIMARY KEY (team_id, player_id, as_of_date)
);

CREATE TABLE IF NOT EXISTS match_events (
    match_id   TEXT NOT NULL REFERENCES matches (match_id),
    event_seq  INTEGER NOT NULL,
    minute     INTEGER,
    event_type TEXT NOT NULL,
    team_id    TEXT REFERENCES teams (team_id),
    player_id  TEXT,
    detail     JSONB,
    PRIMARY KEY (match_id, event_seq)
);

-- Resume/checkpoint ledger. Lives here instead of local SQLite so a run
-- survives a machine change and stays consistent with the data it wrote.
CREATE TABLE IF NOT EXISTS extraction_state (
    entity_type    TEXT NOT NULL,
    entity_id      TEXT NOT NULL,
    competition_id TEXT,
    season_id      TEXT,
    status         TEXT NOT NULL CHECK (status IN ('pending', 'done', 'failed')),
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_extraction_state_status ON extraction_state (status, entity_type);

-- Persisted output of the live decision engine (market/decision.py), written
-- by the `discover-fixtures`/`refresh-odds` cron commands and read by the
-- webapp. Two-phase: a discover pass inserts a 'pending' placeholder as soon
-- as a fixture is known; a refresh pass fills in the real classification and
-- flips status to 'updated'. Keyed by (match_id, rule_version) rather than
-- match_id alone so a rule_version bump never silently reinterprets a row a
-- prior version already decided — the same discipline decision.py already
-- applies to Decision/Zone. home_team_id/away_team_id are stored (not just
-- the names) so the refresh pass can compute team-familiarity counts
-- straight from this table without re-querying the live API for fixture
-- metadata it already has.
--
-- competition_id/season_id/home_team_id/away_team_id all carry FK
-- constraints, same as `matches` does — a live-discovered fixture is not
-- exempt from that, so orchestration.registration registers all four
-- on demand before any insert here, exactly like `backfill` already does
-- for historical data. Never assume a live fixture's league has been
-- backfilled.
CREATE TABLE IF NOT EXISTS predictions (
    match_id          TEXT NOT NULL,
    rule_version      TEXT NOT NULL,
    competition_id    TEXT NOT NULL REFERENCES competitions (competition_id),
    season_id         TEXT NOT NULL REFERENCES seasons (season_id),
    kickoff_utc       TIMESTAMPTZ NOT NULL,
    home_team_id      TEXT NOT NULL REFERENCES teams (team_id),
    away_team_id      TEXT NOT NULL REFERENCES teams (team_id),
    home_team         TEXT NOT NULL,
    away_team         TEXT NOT NULL,
    overdispersion    DOUBLE PRECISION,
    min_prior_matches INTEGER,
    fav_prob          DOUBLE PRECISION,
    call              TEXT NOT NULL CHECK (call IN ('BACK_FAVOURITE', 'NO_BET')),
    decision_zone     TEXT NOT NULL,
    bet_side          TEXT,
    bet_odds          DOUBLE PRECISION,
    edge_estimate     DOUBLE PRECISION,
    skip_reason       TEXT,
    status            TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'updated')),
    first_fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    odds_updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    fetch_count       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (match_id, rule_version)
);

CREATE INDEX IF NOT EXISTS idx_predictions_kickoff ON predictions (kickoff_utc);

-- Derived view, not a physically extracted table: per-team-perspective match
-- history reconstructed from matches + match_team_stats. Feature engineering
-- (rolling averages, decay-weighted form, etc.) reads from this, not from
-- raw match rows directly.
CREATE OR REPLACE VIEW team_form_history AS
SELECT
    m.home_team_id                      AS team_id,
    m.match_id,
    m.competition_id,
    m.season_id,
    m.kickoff_utc,
    'home'::TEXT                        AS venue_role,
    m.home_goals_ft                     AS goals_scored,
    m.away_goals_ft                     AS goals_conceded,
    hs.shots_total, hs.shots_on_target, hs.possession_pct, hs.corners, hs.xg
FROM matches m
LEFT JOIN match_team_stats hs ON hs.match_id = m.match_id AND hs.team_id = m.home_team_id
WHERE m.status = 'finished'

UNION ALL

SELECT
    m.away_team_id                      AS team_id,
    m.match_id,
    m.competition_id,
    m.season_id,
    m.kickoff_utc,
    'away'::TEXT                        AS venue_role,
    m.away_goals_ft                     AS goals_scored,
    m.home_goals_ft                     AS goals_conceded,
    aws.shots_total, aws.shots_on_target, aws.possession_pct, aws.corners, aws.xg
FROM matches m
LEFT JOIN match_team_stats aws ON aws.match_id = m.match_id AND aws.team_id = m.away_team_id
WHERE m.status = 'finished';
