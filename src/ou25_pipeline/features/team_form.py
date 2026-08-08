"""Point-in-time, historically-informed features built on top of the raw
match data — the layer `export/match_csv.py` deliberately stops short of.

The one rule everything here obeys: a feature for a given match uses only
that team's matches strictly *before* the match's kickoff. `standings`
(end-of-season) and `match_team_stats` (post-match) are exactly the kind of
leakage this file exists to avoid reproducing.

Several formulas adapt Columns-Discovery.md (the legacy 1X2 system) —
re-grounded in goals rather than match results, since goals are what this
project actually predicts. Two classes of feature from that document are
deliberately NOT built here (see the project plan): point-in-time league
position (needs a reconstructed running table, not just our end-of-season
snapshot) and model-vs-market divergence features (need a trained model's
own probability estimate to diverge from, which doesn't exist yet).
"""

from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import Engine, select, text

from ou25_pipeline.export.match_csv import TIER_BY_COMPETITION, compute_target
from ou25_pipeline.storage import tables

_ROLLING_WINDOW_SHORT = 3
_ROLLING_WINDOW_LONG = 6
_DECAY_LOOKBACK_MATCHES = 10
_DECAY_HALF_LIFE_DAYS = 30.0

_ROLLING_FEATURE_COLUMNS = [
    "last3_gf", "last3_ga", "last6_gf", "last6_ga",
    "max_gf_last3", "min_gf_last3", "max_ga_last3", "min_ga_last3",
    "decay_weighted_gf", "decay_weighted_ga",
    "last3_xg", "decay_weighted_xg",
    "last3_shots_on_target", "decay_weighted_shots_on_target",
    "gf_strength", "ga_strength", "base_strength",
    "days_since_last_match", "matches_played_prior",
]

FEATURE_NOTES_MD = """\
# Column notes for the features export

## Deferred, not present in this file

- **Point-in-time league table position** (Columns-Discovery.md's `team_league_weight`,
  A.1): our `standings` table is an end-of-season snapshot, not point-in-time.
  Reconstructing "this team's table position as of this specific matchday" needs a
  running table computed from match results, which is a separate, bigger task.
- **Model-vs-market divergence features** (C.1-C.3, C.7-C.13 — `CLV_factor`,
  `Adjusted_Score`, etc.): these compare the market's price against a model's *own*
  probability estimate. No model exists yet — build a baseline on this file's features
  first, then this class of feature becomes buildable.

## matches_played_prior

Rolling/decay features for a team's first few matches in this dataset are based on very
little history (or none — NaN when `matches_played_prior == 0`). Treat rows with a low
`matches_played_prior` (e.g. < 3) as less reliable, not a hard cutoff.

## xg / shots_on_target rolling features

Added after univariate/bivariate EDA on the raw export (matches.csv) showed combined
home+away `xg` and `shots_on_target` are the strongest raw predictors in the entire
dataset (0.42-0.44 correlation with the target — stronger than any single odds line),
well ahead of goals themselves. Both are genuinely sparse for some matches/leagues (not
every match has recorded shot data) — a missing historical match in the decay window is
excluded from that match's contribution rather than nulling the whole feature; the
feature is only NaN if *every* match in the window lacks data.

## No odds-movement feature

Every `_open` odds column in `match_odds` (all 26 markets/lines) is 100% null across the
full dataset — the provider never populates opening prices in practice. There is
therefore no way to compute a close-vs-open movement feature from this data source;
don't be surprised it's absent.

## Season boundaries

Rolling/decay features are computed across a team's full match history regardless of
season — an early-2024-25 match uses late-2023-24 form as context rather than starting
from NaN each season. Liga MX Apertura/Clausura history is blended for the same club
for the same reason (both are the same real-world team).

## Head-to-head

`h2h_matches_played` / `h2h_over_2_5_rate` count prior meetings between this exact pair
of teams regardless of which one was home — symmetric, not home/away-specific.
"""


def _decay_weighted(group: pd.DataFrame, value_col: str) -> pd.Series:
    """Sum of exp(-days_since/half_life) * value over the trailing lookback
    window. `value_col` isn't always populated (xg/shots_on_target are
    genuinely sparse for some leagues — see the coverage note in the raw
    CSV) — a NaN entry is excluded from the sum rather than poisoning the
    whole window; the result is only NaN if *every* entry in the window is
    missing, not just one."""
    kickoffs = group["kickoff_utc"].to_numpy()
    values = group[value_col].to_numpy(dtype=float)
    n = len(group)
    result = np.full(n, np.nan)
    for i in range(n):
        lo = max(0, i - _DECAY_LOOKBACK_MATCHES)
        if lo == i:
            continue  # no prior matches at all
        window = values[lo:i]
        mask = ~np.isnan(window)
        if not mask.any():
            continue
        days = (kickoffs[i] - kickoffs[lo:i][mask]) / np.timedelta64(1, "D")
        weights = np.exp(-days / _DECAY_HALF_LIFE_DAYS)
        result[i] = float(np.sum(weights * window[mask]))
    return pd.Series(result, index=group.index)


def _team_history_frame(engine: Engine) -> pd.DataFrame:
    df = pd.read_sql(text("SELECT * FROM team_form_history"), engine)
    df["kickoff_utc"] = pd.to_datetime(df["kickoff_utc"], utc=True).dt.tz_localize(None)
    return df.sort_values(["team_id", "kickoff_utc"]).reset_index(drop=True)


def _rolling_prior(series: pd.Series, window: int, agg: str) -> pd.Series:
    rolled = getattr(series.rolling(window=window, min_periods=1), agg)()
    return rolled.shift(1)


def _compute_team_rolling_features(history: pd.DataFrame) -> pd.DataFrame:
    grouped = history.groupby("team_id", group_keys=False)

    history["last3_gf"] = grouped["goals_scored"].transform(lambda s: _rolling_prior(s, _ROLLING_WINDOW_SHORT, "sum"))
    history["last3_ga"] = grouped["goals_conceded"].transform(lambda s: _rolling_prior(s, _ROLLING_WINDOW_SHORT, "sum"))
    history["last6_gf"] = grouped["goals_scored"].transform(lambda s: _rolling_prior(s, _ROLLING_WINDOW_LONG, "sum"))
    history["last6_ga"] = grouped["goals_conceded"].transform(lambda s: _rolling_prior(s, _ROLLING_WINDOW_LONG, "sum"))
    history["max_gf_last3"] = grouped["goals_scored"].transform(lambda s: _rolling_prior(s, _ROLLING_WINDOW_SHORT, "max"))
    history["min_gf_last3"] = grouped["goals_scored"].transform(lambda s: _rolling_prior(s, _ROLLING_WINDOW_SHORT, "min"))
    history["max_ga_last3"] = grouped["goals_conceded"].transform(lambda s: _rolling_prior(s, _ROLLING_WINDOW_SHORT, "max"))
    history["min_ga_last3"] = grouped["goals_conceded"].transform(lambda s: _rolling_prior(s, _ROLLING_WINDOW_SHORT, "min"))

    history["decay_weighted_gf"] = grouped.apply(lambda g: _decay_weighted(g, "goals_scored")).reset_index(level=0, drop=True)
    history["decay_weighted_ga"] = grouped.apply(lambda g: _decay_weighted(g, "goals_conceded")).reset_index(level=0, drop=True)

    # xg and shots_on_target: added after EDA on the raw export showed
    # combined home+away values of these two are the strongest raw
    # predictors in the whole dataset (0.42-0.44 correlation with the
    # target — stronger than any single odds line), well ahead of goals
    # themselves. Only last3 + decay (not last6/max/min) — those extra
    # variants were motivated by Columns-Discovery.md's goals-specific A.3,
    # not by anything the EDA asked for here.
    history["last3_xg"] = grouped["xg"].transform(lambda s: _rolling_prior(s, _ROLLING_WINDOW_SHORT, "sum"))
    history["decay_weighted_xg"] = grouped.apply(lambda g: _decay_weighted(g, "xg")).reset_index(level=0, drop=True)
    history["last3_shots_on_target"] = grouped["shots_on_target"].transform(
        lambda s: _rolling_prior(s, _ROLLING_WINDOW_SHORT, "sum")
    )
    history["decay_weighted_shots_on_target"] = grouped.apply(
        lambda g: _decay_weighted(g, "shots_on_target")
    ).reset_index(level=0, drop=True)

    history["gf_strength"] = history["last3_gf"] / 5
    history["ga_strength"] = (2 - history["last3_ga"]) / 2
    history["base_strength"] = history["gf_strength"] + history["ga_strength"]

    history["days_since_last_match"] = grouped["kickoff_utc"].diff().dt.days
    history["matches_played_prior"] = grouped.cumcount()

    return history


def _merge_side(base: pd.DataFrame, history: pd.DataFrame, venue_role: str, side: str) -> pd.DataFrame:
    side_df = history[history["venue_role"] == venue_role][["match_id", *_ROLLING_FEATURE_COLUMNS]]
    renamed = side_df.rename(columns={col: f"{side}_{col}" for col in _ROLLING_FEATURE_COLUMNS})
    return base.merge(renamed, on="match_id", how="left")


def _base_frame(
    engine: Engine, competition_ids: list[str] | None, season_ids: list[str] | None
) -> pd.DataFrame:
    m = tables.matches
    c = tables.competitions
    s = tables.seasons
    stmt = (
        select(
            m.c.match_id,
            m.c.competition_id,
            c.c.name.label("competition_name"),
            m.c.season_id,
            s.c.name.label("season_name"),
            m.c.kickoff_utc,
            m.c.home_team_id,
            m.c.away_team_id,
            m.c.home_goals_ft,
            m.c.away_goals_ft,
        )
        .select_from(m)
        .join(c, c.c.competition_id == m.c.competition_id)
        .join(s, s.c.season_id == m.c.season_id)
    )
    if competition_ids:
        stmt = stmt.where(m.c.competition_id.in_(competition_ids))
    if season_ids:
        stmt = stmt.where(m.c.season_id.in_(season_ids))

    df = pd.read_sql(stmt, engine)
    df["kickoff_utc"] = pd.to_datetime(df["kickoff_utc"], utc=True).dt.tz_localize(None)
    df["total_goals_ft"] = df["home_goals_ft"] + df["away_goals_ft"]
    df["target_over_2_5"] = compute_target(df["total_goals_ft"])
    df["tier"] = df["competition_id"].map(TIER_BY_COMPETITION)
    return df


def _odds_frame(engine: Engine) -> pd.DataFrame:
    # `_open` columns are deliberately not selected: checked against the
    # full dataset and every single one (all 26 markets/lines) is 100% null
    # — the provider never populates opening prices in practice. A prior
    # version of this module computed an odds-movement feature from
    # close-open that was therefore always NaN; removed rather than fixed,
    # since there's no data to compute a movement from at all.
    o = tables.match_odds
    stmt = select(
        o.c.match_id,
        o.c.goals_2_5_over_close,
        o.c.goals_2_5_under_close,
        o.c.btts_yes_odds_close,
        o.c.btts_no_odds_close,
        o.c.home_odds_close,
        o.c.draw_odds_close,
        o.c.away_odds_close,
    )
    df = pd.read_sql(stmt, engine)
    if df.empty:
        return pd.DataFrame(columns=["match_id"])
    # Only one bookmaker (Bet365) observed in practice; guard regardless.
    return df.groupby("match_id", as_index=False).first()


def _devig(*odds_columns: pd.Series) -> list[pd.Series]:
    """Overround-removed implied probabilities for an n-way market —
    adapts Columns-Discovery.md B.2's de-vigging math (there, 3-way 1X2;
    here, applied generically to whatever market is passed in)."""
    implied = [1 / o for o in odds_columns]
    overround = sum(implied)
    return [i / overround for i in implied]


def _add_market_features(base: pd.DataFrame) -> pd.DataFrame:
    base["implied_prob_over_2_5"], base["implied_prob_under_2_5"] = _devig(
        base["goals_2_5_over_close"], base["goals_2_5_under_close"]
    )
    base["implied_prob_btts_yes"], base["implied_prob_btts_no"] = _devig(
        base["btts_yes_odds_close"], base["btts_no_odds_close"]
    )
    base["implied_prob_home_win"], base["implied_prob_draw"], base["implied_prob_away_win"] = _devig(
        base["home_odds_close"], base["draw_odds_close"], base["away_odds_close"]
    )
    return base


def _add_head_to_head(base: pd.DataFrame) -> pd.DataFrame:
    # Reset to a plain 0..n-1 RangeIndex so "position" and "label" coincide —
    # avoids any ambiguity mixing pandas label-based and positional indexing.
    base = base.sort_values("kickoff_utc").reset_index(drop=True)
    pair_key = base.apply(lambda r: "_".join(sorted([r["home_team_id"], r["away_team_id"]])), axis=1)
    target_numeric = base["target_over_2_5"].map({True: 1.0, False: 0.0}).to_numpy()

    played = np.zeros(len(base), dtype="int64")
    rate = np.full(len(base), np.nan)

    for _, positions in pd.Series(range(len(base))).groupby(pair_key.to_numpy()):
        ordered = sorted(positions)  # already kickoff_utc-sorted by construction
        for i, pos in enumerate(ordered):
            prior = target_numeric[ordered[:i]]
            prior = prior[~np.isnan(prior)]
            played[pos] = len(prior)
            if len(prior) > 0:
                rate[pos] = float(prior.mean())

    base["h2h_matches_played"] = played
    base["h2h_over_2_5_rate"] = rate
    return base


def build_feature_dataframe(
    engine: Engine,
    competition_ids: list[str] | None = None,
    season_ids: list[str] | None = None,
) -> pd.DataFrame:
    """One row per match: point-in-time rolling/decay team form, market-
    derived, and head-to-head features. See module docstring for scope."""
    base = _base_frame(engine, competition_ids, season_ids)
    if base.empty:
        return base

    history = _team_history_frame(engine)
    history = _compute_team_rolling_features(history)

    base = _merge_side(base, history, "home", "home")
    base = _merge_side(base, history, "away", "away")

    odds = _odds_frame(engine)
    base = base.merge(odds, on="match_id", how="left")
    base = _add_market_features(base)

    base = _add_head_to_head(base)

    return base.sort_values("kickoff_utc").reset_index(drop=True)


def write_csv(df: pd.DataFrame, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)

    notes_path = output_path.with_suffix(output_path.suffix + ".columns.md")
    notes_path.write_text(FEATURE_NOTES_MD)
    return notes_path
