"""SQLAlchemy Core table definitions used to build parameterized upsert
statements. `db/schema.sql` is the source of truth for DDL (types,
constraints, the generated columns, the team_form_history view) — these
mirror it just enough to construct INSERT ... ON CONFLICT statements safely,
rather than hand-building SQL strings per table.
"""

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    Float,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

metadata = MetaData()

competitions = Table(
    "competitions",
    metadata,
    Column("competition_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("country", Text),
    Column("type", Text),
    Column("is_tracked", Boolean, nullable=False, server_default="false"),
    Column("tier", Integer),
)

seasons = Table(
    "seasons",
    metadata,
    Column("season_id", Text, primary_key=True),
    Column("competition_id", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("year", Text, nullable=False),
    Column("start_year", Integer),
    Column("end_year", Integer),
    Column("is_current", Boolean, nullable=False, default=False),
)

teams = Table(
    "teams",
    metadata,
    Column("team_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("country", Text),
)

referees = Table(
    "referees",
    metadata,
    Column("referee_id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("country", Text),
    Column("career_games", Integer),
    Column("career_yellow", Integer),
    Column("career_red", Integer),
    Column("career_yellow_red", Integer),
)

standings = Table(
    "standings",
    metadata,
    Column("season_id", Text, primary_key=True),
    Column("team_id", Text, primary_key=True),
    Column("group_label", Text, primary_key=True, default=""),
    Column("position", Integer),
    Column("total_teams", Integer),
    Column("matches_played", Integer),
    Column("wins", Integer),
    Column("draws", Integer),
    Column("losses", Integer),
    Column("goals_for", Integer),
    Column("goals_against", Integer),
    Column("goal_difference", Integer),
    Column("points", Integer),
)

matches = Table(
    "matches",
    metadata,
    Column("match_id", Text, primary_key=True),
    Column("competition_id", Text, nullable=False),
    Column("season_id", Text, nullable=False),
    Column("matchday", Integer),
    Column("stage_name", Text),
    Column("group_label", Text),
    Column("kickoff_utc", TIMESTAMP(timezone=True), nullable=False),
    Column("home_team_id", Text, nullable=False),
    Column("away_team_id", Text, nullable=False),
    Column("venue_name", Text),
    Column("venue_city", Text),
    Column("referee_id", Text),
    Column("home_goals_ft", Integer),
    Column("away_goals_ft", Integer),
    Column("home_goals_ht", Integer),
    Column("away_goals_ht", Integer),
    Column("status", Text, nullable=False),
    Column("odds_available", Boolean, nullable=False, default=False),
    Column("xg_available", Boolean, nullable=False, default=False),
)

match_team_stats = Table(
    "match_team_stats",
    metadata,
    Column("match_id", Text, primary_key=True),
    Column("team_id", Text, primary_key=True),
    Column("side", Text, nullable=False),
    Column("shots_total", Integer),
    Column("shots_on_target", Integer),
    Column("possession_pct", Numeric(5, 2)),
    Column("corners", Integer),
    Column("xg", Numeric(5, 2)),
)

def _odds_pair_columns() -> list[Column]:
    """Wide, not long: over_open/over_close/under_open/under_close side by
    side per line, rather than a self-join to find the opposite side of the
    same market. Lines are the fixed, empirically observed set 0.5-9.5 —
    kept in sync with db/schema.sql and orchestration.mapping.TOTAL_GOALS_LINES."""
    columns = []
    for line in ("0_5", "1_5", "2_5", "3_5", "4_5", "5_5", "6_5", "7_5", "8_5", "9_5"):
        for selection in ("over", "under"):
            for side in ("open", "close"):
                columns.append(Column(f"goals_{line}_{selection}_{side}", Numeric(8, 3)))
    return columns


match_odds = Table(
    "match_odds",
    metadata,
    Column("match_id", Text, primary_key=True),
    Column("bookmaker", Text, primary_key=True),
    Column("home_odds_open", Numeric(8, 3)),
    Column("home_odds_close", Numeric(8, 3)),
    Column("draw_odds_open", Numeric(8, 3)),
    Column("draw_odds_close", Numeric(8, 3)),
    Column("away_odds_open", Numeric(8, 3)),
    Column("away_odds_close", Numeric(8, 3)),
    Column("btts_yes_odds_open", Numeric(8, 3)),
    Column("btts_yes_odds_close", Numeric(8, 3)),
    Column("btts_no_odds_open", Numeric(8, 3)),
    Column("btts_no_odds_close", Numeric(8, 3)),
    *_odds_pair_columns(),
)

match_lineups = Table(
    "match_lineups",
    metadata,
    Column("match_id", Text, primary_key=True),
    Column("team_id", Text, primary_key=True),
    Column("side", Text, nullable=False),
    Column("confirmed", Boolean, nullable=False, default=False),
    Column("formation", Text),
)

match_lineup_players = Table(
    "match_lineup_players",
    metadata,
    Column("match_id", Text, primary_key=True),
    Column("team_id", Text, primary_key=True),
    Column("player_id", Text, primary_key=True),
    Column("is_starter", Boolean, nullable=False),
)

team_injuries_suspensions = Table(
    "team_injuries_suspensions",
    metadata,
    Column("team_id", Text, primary_key=True),
    Column("player_id", Text, primary_key=True),
    Column("type", Text, nullable=False),
    Column("status", Text),
    Column("as_of_date", Date, primary_key=True),
)

match_events = Table(
    "match_events",
    metadata,
    Column("match_id", Text, primary_key=True),
    Column("event_seq", Integer, primary_key=True),
    Column("minute", Integer),
    Column("event_type", Text, nullable=False),
    Column("team_id", Text),
    Column("player_id", Text),
    Column("detail", JSONB),
)

extraction_state = Table(
    "extraction_state",
    metadata,
    Column("entity_type", Text, primary_key=True),
    Column("entity_id", Text, primary_key=True),
    Column("competition_id", Text),
    Column("season_id", Text),
    Column("status", Text, nullable=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("last_error", Text),
)

predictions = Table(
    "predictions",
    metadata,
    Column("match_id", Text, primary_key=True),
    Column("rule_version", Text, primary_key=True),
    Column("competition_id", Text, nullable=False),
    Column("season_id", Text, nullable=False),
    Column("kickoff_utc", TIMESTAMP(timezone=True), nullable=False),
    Column("home_team_id", Text, nullable=False),
    Column("away_team_id", Text, nullable=False),
    Column("home_team", Text, nullable=False),
    Column("away_team", Text, nullable=False),
    Column("overdispersion", Float),
    Column("min_prior_matches", Integer),
    Column("fav_prob", Float),
    Column("call", Text, nullable=False),
    Column("decision_zone", Text, nullable=False),
    Column("bet_side", Text),
    Column("bet_odds", Float),
    Column("edge_estimate", Float),
    Column("skip_reason", Text),
    Column("status", Text, nullable=False, default="pending"),
    Column("odds_updated_at", TIMESTAMP(timezone=True)),
    Column("fetch_count", Integer, nullable=False, default=0),
)
