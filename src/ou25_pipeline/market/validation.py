"""Per-competition validation: how a new competition earns its way into
`decision.VALIDATED_COMPETITIONS`.

The original 13 competitions were validated as a POOL (n=936/1,621 zone
bets) — no single competition was ever individually held to "OOS CI
excludes zero", and none could be: one competition produces ~50-70 zone
bets a season, and that bar alone needs ~1,500. Demanding it per-comp
would freeze the validated set forever.

What makes honest per-comp admission possible at all: the rule's
thresholds were frozen before ever seeing a candidate's data, so a
never-calibrated competition's ENTIRE history is out-of-sample by
construction. Every bet the frozen rule would have placed there is a
genuine OOS observation — no threshold re-derivation, no walk-forward
blocks needed.

Admission criteria — PRE-REGISTERED, fixed here before any candidate is
examined, so passing comps are earned rather than cherry-picked:

1. n_zone >= MIN_ZONE_BETS  in-zone bets exactly as v3 would place them
   (broad zone, min_prior >= 4, layoff filter applied). Below this the
   verdict is INSUFFICIENT_DATA, not FAIL — backfill more seasons.
2. edge_pp > 0              the favourite beats its own price on average.
3. ROI > 0 with P(ROI>0) >= MIN_P_POSITIVE (bootstrap).

Under a true-zero edge, criterion 3 alone false-admits ~10% of
candidates — accepted and stated, since the alternative (the pool bar)
admits nobody. A comp that passes gets added to VALIDATED_COMPETITIONS
in a new rule_version; forward tracking then judges it for real.

Runs entirely from the database — zero live API calls, so checking a
candidate is free regardless of quota.

Two deliberately conservative approximations, both erring toward
skipping bets rather than inventing them: `min_prior_matches` and the
layoff gap are computed from the competition's own matches only, so a
team's cross-competition appearances aren't counted (slightly
undercounts history, slightly overstates gaps).
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sqlalchemy import Engine, text

from ou25_pipeline.market.backtest import bootstrap_roi, returns
from ou25_pipeline.market.decision import LAYOFF_MAX_DAYS, ZONES, favourite_frame
from ou25_pipeline.market.ladder import build_ladder_features, prior_match_counts

MIN_ZONE_BETS = 100
MIN_P_POSITIVE = 0.90

_LINES = ("0_5", "1_5", "2_5", "3_5", "4_5", "5_5", "6_5")


@dataclass(frozen=True)
class CompetitionValidation:
    competition_id: str
    n_matches: int          # finished matches with a priced 2.5 line
    n_fitted: int           # of those, ladder fit succeeded
    n_zone: int             # bets v3 would actually have placed
    edge_pp: float
    roi: float
    ci_low: float
    ci_high: float
    p_positive: float
    thirds: list[dict]      # stability report, not a gate
    verdict: str            # 'PASS' | 'FAIL' | 'INSUFFICIENT_DATA'
    detail: str


def build_competition_frame(engine: Engine, competition_id: str) -> pd.DataFrame:
    """Frame the competition's full stored history exactly the way the
    engine sees a match: one bookmaker per match (Bet365 preferred — the
    book every threshold was calibrated against), ladder fit, favourite
    framing, point-in-time prior counts and layoff gaps."""
    cols = ", ".join(f"o.goals_{l}_over_close, o.goals_{l}_under_close" for l in _LINES)
    df = pd.read_sql(text(f"""
        SELECT m.match_id, o.bookmaker, m.kickoff_utc, m.home_team_id, m.away_team_id,
               m.home_goals_ft, m.away_goals_ft, {cols}
        FROM matches m JOIN match_odds o ON o.match_id = m.match_id
        WHERE m.competition_id = :c AND m.status = 'finished' AND m.home_goals_ft IS NOT NULL
    """), engine, params={"c": competition_id})
    if df.empty:
        return df
    df["_pref"] = (df.bookmaker != "Bet365").astype(int)
    df = df.sort_values(["match_id", "_pref"]).groupby("match_id", as_index=False).first().drop(columns="_pref")
    df["kickoff_utc"] = pd.to_datetime(df.kickoff_utc, utc=True)
    for c in df.columns:
        if c.startswith("goals_"):
            df[c] = df[c].astype(float)
    df = df[df.goals_2_5_over_close.notna() & df.goals_2_5_under_close.notna()].copy()
    if df.empty:
        return df

    # quoted_prob_over_2_5 is produced by build_ladder_features below —
    # computing it here too would duplicate the column and break
    # favourite_frame's scalar selection.
    df["target_over_2_5"] = (df.home_goals_ft + df.away_goals_ft) > 2.5
    df["min_prior_matches"] = prior_match_counts(df)

    df = df.sort_values("kickoff_utc").reset_index(drop=True)
    long = pd.concat([
        df[["match_id", "kickoff_utc", "home_team_id"]].rename(columns={"home_team_id": "team_id"}),
        df[["match_id", "kickoff_utc", "away_team_id"]].rename(columns={"away_team_id": "team_id"}),
    ]).sort_values("kickoff_utc")
    long["gap"] = (long.kickoff_utc - long.groupby("team_id").kickoff_utc.shift(1)).dt.total_seconds() / 86400
    gap = long.set_index(["match_id", "team_id"]).gap
    df["stale_gap"] = [
        max(g for g in (gap.get((mid, h), np.nan), gap.get((mid, a), np.nan)))
        for mid, h, a in zip(df.match_id, df.home_team_id, df.away_team_id)
    ]

    df = pd.concat([df, build_ladder_features(df)], axis=1)
    return favourite_frame(df)


def evaluate_frame(df: pd.DataFrame, competition_id: str) -> CompetitionValidation:
    """Apply the pre-registered criteria to a framed history. Split from
    the DB/fitting work so the admission logic is unit-testable."""
    n_matches = len(df)
    fitted = df[df.get("overdispersion", pd.Series(dtype=float)).notna()] if n_matches else df
    broad = ZONES[-1]
    zone = fitted[
        (fitted.overdispersion <= broad.max_overdispersion)
        & (fitted.min_prior_matches.astype(float) >= broad.min_prior_matches)
        & ~(fitted.stale_gap > LAYOFF_MAX_DAYS)
    ] if len(fitted) else fitted

    if len(zone) < MIN_ZONE_BETS:
        return CompetitionValidation(
            competition_id, n_matches, len(fitted), len(zone),
            float("nan"), float("nan"), float("nan"), float("nan"), float("nan"), [],
            "INSUFFICIENT_DATA",
            f"{len(zone)} zone bets, need >= {MIN_ZONE_BETS} — backfill more seasons and re-run",
        )

    won = zone.fav_won.to_numpy(dtype=bool)
    profit = returns(won, zone.fav_odds.to_numpy(dtype=float))
    boot = bootstrap_roi(profit)
    edge_pp = float((won.mean() - zone.fav_prob.mean()) * 100)

    ordered = zone.sort_values("kickoff_utc")
    edges = np.linspace(0, len(ordered), 4).astype(int)
    thirds = []
    for i, (lo, hi) in enumerate(zip(edges[:-1], edges[1:]), start=1):
        part = ordered.iloc[lo:hi]
        part_won = part.fav_won.to_numpy(dtype=bool)
        thirds.append({
            "third": i, "n": len(part),
            "roi": float(returns(part_won, part.fav_odds.to_numpy(dtype=float)).mean()),
        })

    checks = {
        "edge_pp > 0": edge_pp > 0,
        "roi > 0": boot["roi"] > 0,
        f"P(ROI>0) >= {MIN_P_POSITIVE}": boot["p_positive"] >= MIN_P_POSITIVE,
    }
    failed = [name for name, ok in checks.items() if not ok]
    verdict = "PASS" if not failed else "FAIL"
    detail = "all criteria met" if not failed else "failed: " + ", ".join(failed)
    return CompetitionValidation(
        competition_id, n_matches, len(fitted), len(zone), edge_pp,
        boot["roi"], boot["ci_low"], boot["ci_high"], boot["p_positive"],
        thirds, verdict, detail,
    )


def validate_competition(engine: Engine, competition_id: str) -> CompetitionValidation:
    return evaluate_frame(build_competition_frame(engine, competition_id), competition_id)
