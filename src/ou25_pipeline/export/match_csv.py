"""Flattens the normalized Postgres tables into one row-per-match DataFrame
for exploratory data analysis. Deliberately stops at directly-available or
simply-joined fields — no rolling windows, no decay-weighted form, nothing
from Columns-Discovery.md's derived-column formulas. That's a separate,
later feature-engineering phase once EDA has shown what's worth building.
"""

from pathlib import Path

import pandas as pd
from sqlalchemy import Engine, func, select

from ou25_pipeline.storage import tables

# The 13 competition rows backfilled so far. Grouping key for EDA — not
# used anywhere in extraction. An unrecognized competition_id (a future
# league) just gets tier=None rather than raising.
TIER_BY_COMPETITION = {
    "comp_3039": 1,  # Premier League
    "comp_8814": 1,  # LaLiga
    "comp_4643": 1,  # Bundesliga
    "comp_5840": 1,  # Serie A
    "comp_0256": 1,  # Ligue 1
    "comp_3809": 2,  # Eredivisie
    "comp_8385": 2,  # Liga Portugal Betclic
    "comp_8321": 2,  # Championship
    "comp_4795": 2,  # Brasileirão Série A
    "comp_8531": 3,  # Belgian Pro League
    "comp_298265": 3,  # Liga MX, Apertura
    "comp_137103": 3,  # Liga MX, Clausura
    "comp_9799": 3,  # MLS
}

_CARD_EVENT_TYPES = ("red_card", "yellow_card", "yellow_red_card")

COLUMN_NOTES_MD = """\
# Column notes for the matches export

## Post-match statistics (not safe as pre-match features without lagging)

home_shots_total, home_shots_on_target, home_possession_pct, home_corners, home_xg,
away_shots_total, away_shots_on_target, away_possession_pct, away_corners, away_xg,
home_red_cards, home_yellow_cards, away_red_cards, away_yellow_cards

These describe what happened *during* the match itself. They're useful for exploring
correlations between match events and the Over/Under 2.5 outcome, but must never be fed
into a pre-match prediction model for this same match — that's data leakage. They're
only safe once lagged into a rolling "team's last N matches" feature for a *different*,
future match.

## End-of-season standings (not point-in-time)

home_final_position, home_final_points, home_final_goal_difference,
home_final_total_teams_in_league, away_final_position, away_final_points,
away_final_goal_difference, away_final_total_teams_in_league

These are the season's FINAL table standing, pulled after the season ended. For a match
played mid-season, this leaks results from games played after it. Only safe for
post-hoc analysis of a completed season, not as a pre-match prediction feature.

## Playoff-stage leagues collapse to regular-season standings only

Belgian Pro League (and similarly-shaped leagues) split into a regular season plus
separate playoff tables. The final_* columns above use the regular-season standing only
— "final league position after playoffs" isn't a single well-defined number for these
leagues in the current schema.
"""


def compute_target(total_goals: pd.Series) -> pd.Series:
    result = pd.Series(pd.NA, index=total_goals.index, dtype="boolean")
    known = total_goals.notna()
    result.loc[known] = total_goals.loc[known] >= 3
    return result


def _base_frame(
    engine: Engine, competition_ids: list[str] | None, season_ids: list[str] | None
) -> pd.DataFrame:
    m = tables.matches
    c = tables.competitions
    s = tables.seasons
    ht = tables.teams.alias("ht")
    at = tables.teams.alias("at")
    r = tables.referees

    stmt = (
        select(
            m.c.match_id,
            m.c.competition_id,
            c.c.name.label("competition_name"),
            m.c.season_id,
            s.c.name.label("season_name"),
            m.c.matchday,
            m.c.stage_name,
            m.c.kickoff_utc,
            m.c.venue_name,
            m.c.venue_city,
            m.c.home_team_id,
            ht.c.name.label("home_team_name"),
            m.c.away_team_id,
            at.c.name.label("away_team_name"),
            m.c.referee_id,
            r.c.name.label("referee_name"),
            r.c.career_games.label("referee_career_games"),
            r.c.career_yellow.label("referee_career_yellow"),
            r.c.career_red.label("referee_career_red"),
            r.c.career_yellow_red.label("referee_career_yellow_red"),
            m.c.home_goals_ft,
            m.c.away_goals_ft,
            m.c.home_goals_ht,
            m.c.away_goals_ht,
            m.c.odds_available,
            m.c.xg_available,
        )
        .select_from(m)
        .join(c, c.c.competition_id == m.c.competition_id)
        .join(s, s.c.season_id == m.c.season_id)
        .join(ht, ht.c.team_id == m.c.home_team_id)
        .join(at, at.c.team_id == m.c.away_team_id)
        .outerjoin(r, r.c.referee_id == m.c.referee_id)
    )
    if competition_ids:
        stmt = stmt.where(m.c.competition_id.in_(competition_ids))
    if season_ids:
        stmt = stmt.where(m.c.season_id.in_(season_ids))
    return pd.read_sql(stmt, engine)


def _team_stats_frame(engine: Engine) -> pd.DataFrame:
    ts = tables.match_team_stats
    stmt = select(
        ts.c.match_id,
        ts.c.side,
        ts.c.shots_total,
        ts.c.shots_on_target,
        ts.c.possession_pct,
        ts.c.corners,
        ts.c.xg,
    )
    df = pd.read_sql(stmt, engine)
    if df.empty:
        return pd.DataFrame(columns=["match_id"])
    wide = df.pivot(
        index="match_id",
        columns="side",
        values=["shots_total", "shots_on_target", "possession_pct", "corners", "xg"],
    )
    wide.columns = [f"{side}_{value}" for value, side in wide.columns]
    return wide.reset_index()


def _odds_frame(engine: Engine) -> pd.DataFrame:
    o = tables.match_odds
    cols = [col for col in o.columns if col.name != "bookmaker"]
    df = pd.read_sql(select(*cols), engine)
    if df.empty:
        return df
    # Only one bookmaker (Bet365) observed in practice, but guard against a
    # future match having more than one row per match_id regardless.
    return df.groupby("match_id", as_index=False).first()


def _lineup_frame(engine: Engine) -> pd.DataFrame:
    lu = tables.match_lineups
    df = pd.read_sql(select(lu.c.match_id, lu.c.side, lu.c.confirmed, lu.c.formation), engine)
    if df.empty:
        return pd.DataFrame(columns=["match_id"])
    wide = df.pivot(index="match_id", columns="side", values="formation")
    wide.columns = [f"{side}_formation" for side in wide.columns]
    confirmed = df.groupby("match_id")["confirmed"].first().rename("lineups_confirmed")
    return wide.join(confirmed).reset_index()


def _card_counts_frame(engine: Engine, base: pd.DataFrame) -> pd.DataFrame:
    e = tables.match_events
    stmt = (
        select(e.c.match_id, e.c.team_id, e.c.event_type, func.count().label("n"))
        .where(e.c.event_type.in_(_CARD_EVENT_TYPES))
        .group_by(e.c.match_id, e.c.team_id, e.c.event_type)
    )
    df = pd.read_sql(stmt, engine)
    if df.empty:
        return pd.DataFrame(columns=["match_id"])

    # A second yellow (yellow_red_card) is a sending-off — counts as a red.
    df["event_type"] = df["event_type"].replace("yellow_red_card", "red_card")
    df = df.groupby(["match_id", "team_id", "event_type"], as_index=False)["n"].sum()

    side_map = pd.concat(
        [
            base[["match_id", "home_team_id"]]
            .rename(columns={"home_team_id": "team_id"})
            .assign(side="home"),
            base[["match_id", "away_team_id"]]
            .rename(columns={"away_team_id": "team_id"})
            .assign(side="away"),
        ]
    )
    df = df.merge(side_map, on=["match_id", "team_id"], how="inner")
    wide = df.pivot_table(
        index="match_id", columns=["side", "event_type"], values="n", fill_value=0
    )
    wide.columns = [f"{side}_{event_type.replace('_card', '')}_cards" for side, event_type in wide.columns]
    return wide.reset_index()


def _standings_frame(engine: Engine) -> pd.DataFrame:
    st = tables.standings
    stmt = select(
        st.c.season_id,
        st.c.team_id,
        st.c.position,
        st.c.points,
        st.c.goal_difference,
        st.c.total_teams,
    ).where(st.c.group_label == "")
    return pd.read_sql(stmt, engine)


def _merge_standings(base: pd.DataFrame, standings: pd.DataFrame, side: str) -> pd.DataFrame:
    if standings.empty:
        for col in ("final_position", "final_points", "final_goal_difference", "final_total_teams_in_league"):
            base[f"{side}_{col}"] = pd.NA
        return base
    renamed = standings.rename(
        columns={
            "team_id": f"{side}_team_id",
            "position": f"{side}_final_position",
            "points": f"{side}_final_points",
            "goal_difference": f"{side}_final_goal_difference",
            "total_teams": f"{side}_final_total_teams_in_league",
        }
    )
    return base.merge(renamed, on=["season_id", f"{side}_team_id"], how="left")


def build_match_dataframe(
    engine: Engine,
    competition_ids: list[str] | None = None,
    season_ids: list[str] | None = None,
) -> pd.DataFrame:
    """One row per match. See module docstring for scope boundaries."""
    base = _base_frame(engine, competition_ids, season_ids)
    if base.empty:
        return base

    base["total_goals_ft"] = base["home_goals_ft"] + base["away_goals_ft"]
    base["target_over_2_5"] = compute_target(base["total_goals_ft"])

    base = base.merge(_team_stats_frame(engine), on="match_id", how="left")
    base = base.merge(_odds_frame(engine), on="match_id", how="left")
    base = base.merge(_lineup_frame(engine), on="match_id", how="left")

    base = base.merge(_card_counts_frame(engine, base), on="match_id", how="left")
    for col in ("home_red_cards", "home_yellow_cards", "away_red_cards", "away_yellow_cards"):
        base[col] = base[col].fillna(0).astype(int) if col in base.columns else 0

    standings = _standings_frame(engine)
    base = _merge_standings(base, standings, "home")
    base = _merge_standings(base, standings, "away")

    base["tier"] = base["competition_id"].map(TIER_BY_COMPETITION)

    return base.sort_values("kickoff_utc").reset_index(drop=True)


def write_csv(df: pd.DataFrame, output_path: Path) -> Path:
    """Writes the CSV plus a sidecar `<output>.columns.md` caveat file so
    the leakage/point-in-time notes travel with the data, not just in a
    plan document. Returns the sidecar path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)

    notes_path = output_path.with_suffix(output_path.suffix + ".columns.md")
    notes_path.write_text(COLUMN_NOTES_MD)
    return notes_path
