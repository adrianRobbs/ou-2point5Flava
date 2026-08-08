"""Tests for the pre-registered per-competition admission criteria.

`evaluate_frame` is split from the DB/ladder-fitting work precisely so
the criteria logic tests run without a database or a slow NB fit — the
frames here carry pre-computed overdispersion/fav columns."""

import numpy as np
import pandas as pd

from ou25_pipeline.market.decision import ZONES
from ou25_pipeline.market.validation import MIN_ZONE_BETS, evaluate_frame

_BROAD = ZONES[-1]


def _zone_frame(n: int, win_rate: float, odds: float = 1.9, fav_prob: float = 0.52,
                seed: int = 0) -> pd.DataFrame:
    """n in-zone matches with a chosen realized win rate — every row passes
    the zone/familiarity/layoff masks, so the criteria alone decide."""
    rng = np.random.default_rng(seed)
    won = np.zeros(n, dtype=bool)
    won[: int(round(n * win_rate))] = True
    rng.shuffle(won)
    return pd.DataFrame({
        "match_id": [f"mt_{i}" for i in range(n)],
        "kickoff_utc": pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC"),
        "overdispersion": _BROAD.max_overdispersion - 0.005,
        "min_prior_matches": 10,
        "stale_gap": 7.0,
        "fav_won": won,
        "fav_odds": odds,
        "fav_prob": fav_prob,
    })


def test_insufficient_data_below_minimum_zone_bets():
    result = evaluate_frame(_zone_frame(MIN_ZONE_BETS - 1, 0.60), "comp_x")
    assert result.verdict == "INSUFFICIENT_DATA"
    assert result.n_zone == MIN_ZONE_BETS - 1


def test_empty_history_is_insufficient_not_a_crash():
    result = evaluate_frame(pd.DataFrame(), "comp_x")
    assert result.verdict == "INSUFFICIENT_DATA"
    assert result.n_matches == 0


def test_clearly_profitable_competition_passes():
    # 60% strike at 1.9 -> ROI +14%; comfortably clears every criterion.
    result = evaluate_frame(_zone_frame(200, 0.60), "comp_x")
    assert result.verdict == "PASS"
    assert result.roi > 0
    assert result.p_positive >= 0.90
    assert len(result.thirds) == 3


def test_losing_competition_fails():
    # 48% strike at 1.9 -> ROI -8.8%.
    result = evaluate_frame(_zone_frame(200, 0.48), "comp_x")
    assert result.verdict == "FAIL"
    assert "roi > 0" in result.detail or "edge_pp" in result.detail or "P(ROI>0)" in result.detail


def test_marginal_positive_roi_without_confidence_fails():
    # 53.2% at 1.9 -> ROI ~ +1.1%: positive but P(ROI>0) nowhere near 0.90
    # at n=200 - exactly the "lucky so far, not proven" case the
    # confidence criterion exists to reject.
    result = evaluate_frame(_zone_frame(200, 0.532), "comp_x")
    assert result.roi > 0
    assert result.verdict == "FAIL"
    assert "P(ROI>0)" in result.detail


def test_out_of_zone_matches_are_not_counted_as_bets():
    df = _zone_frame(150, 0.60)
    df.loc[df.index[:100], "overdispersion"] = _BROAD.max_overdispersion + 0.05
    result = evaluate_frame(df, "comp_x")
    assert result.n_zone == 50
    assert result.verdict == "INSUFFICIENT_DATA"


def test_layoff_matches_are_excluded_from_zone_bets():
    df = _zone_frame(150, 0.60)
    df.loc[df.index[:30], "stale_gap"] = 90.0
    result = evaluate_frame(df, "comp_x")
    assert result.n_zone == 120
