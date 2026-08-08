import numpy as np
import pandas as pd
import pytest

from ou25_pipeline.market.backtest import bootstrap_roi, returns, run_backtest, zone_mask
from ou25_pipeline.market.decision import (
    BACK_FAVOURITE,
    NO_BET,
    RULE_VERSION,
    ZONES,
    classify,
    classify_frame,
    favourite_frame,
)
from ou25_pipeline.market.ladder import (
    LADDER_LINES,
    build_ladder_features,
    devig_pair,
    fit_ladder,
    prior_match_counts,
    survival_curve,
)


def _ladder_odds(mu: float, overround: float = 1.057) -> dict:
    """Fair Poisson-consistent prices for every line, with margin added."""
    from scipy.stats import poisson

    odds = {}
    for line in LADDER_LINES:
        p_over = float(1 - poisson.cdf(int(line), mu))
        p_over = min(max(p_over, 1e-4), 1 - 1e-4)
        odds[line] = (1 / (p_over * overround), 1 / ((1 - p_over) * overround))
    return odds


def test_devig_pair_removes_overround():
    # 1/1.90 + 1/2.00 = 1.0263 overround; de-vigged sides must sum to 1.
    p_over = devig_pair(1.90, 2.00)
    p_under = devig_pair(2.00, 1.90)
    assert abs(p_over + p_under - 1.0) < 1e-12
    assert abs(p_over - (1 / 1.90) / (1 / 1.90 + 1 / 2.00)) < 1e-12


def test_devig_pair_is_insensitive_to_margin_size():
    """De-vigging must recover the same probability regardless of how much
    margin the book applied — otherwise ladder lines with different margins
    would produce an inconsistent curve."""
    fair_over, fair_under = 1 / 0.6, 1 / 0.4
    for margin in (1.02, 1.06, 1.10):
        p = devig_pair(fair_over / margin, fair_under / margin)
        assert abs(p - 0.6) < 1e-9, margin


def test_fit_ladder_recovers_a_known_distribution():
    """A ladder priced from Poisson(2.6) must fit back to mean ~2.6 with
    overdispersion ~1 — the sanity check that the fit reads shape correctly."""
    counts, survival = survival_curve(_ladder_odds(2.6))
    fit = fit_ladder(counts, survival, exclude_line=None)
    assert fit is not None
    assert abs(fit.mu - 2.6) < 0.15
    assert abs(fit.overdispersion - 1.0) < 0.12


def test_fit_ladder_detects_overdispersion():
    """A genuinely fat-tailed ladder must read as more overdispersed than a
    Poisson one. This is the signal the whole engine rests on, so a silent
    failure here would be expensive."""
    from scipy.stats import nbinom

    mu, r = 2.8, 4.0
    odds = {}
    for line in LADDER_LINES:
        p_over = float(1 - nbinom.cdf(int(line), r, r / (r + mu)))
        p_over = min(max(p_over, 1e-4), 1 - 1e-4)
        odds[line] = (1 / (p_over * 1.057), 1 / ((1 - p_over) * 1.057))

    fat = fit_ladder(*survival_curve(odds), exclude_line=None)
    thin = fit_ladder(*survival_curve(_ladder_odds(2.8)), exclude_line=None)
    assert fat.overdispersion > thin.overdispersion


def test_fit_ladder_excludes_the_target_line_by_default():
    counts, survival = survival_curve(_ladder_odds(2.7))
    default_fit = fit_ladder(counts, survival)
    full_fit = fit_ladder(counts, survival, exclude_line=None)
    assert default_fit.n_lines == full_fit.n_lines - 1


def test_fit_ladder_returns_none_when_too_few_lines():
    odds = {0.5: (1.05, 12.0), 1.5: (1.25, 4.0)}
    assert fit_ladder(*survival_curve(odds)) is None


def test_classify_backs_favourite_only_inside_a_zone():
    tight, broad = ZONES[0], ZONES[1]
    inside = classify(tight.max_overdispersion - 0.001, 10, "OVER", 1.8)
    assert inside.call == BACK_FAVOURITE
    assert inside.zone == tight.name
    assert inside.rule_version == RULE_VERSION

    outside = classify(broad.max_overdispersion + 0.05, 10, "OVER", 1.8)
    assert outside.call == NO_BET
    assert outside.zone == "none"


def test_classify_prefers_the_tighter_zone():
    tight = ZONES[0]
    assert classify(tight.max_overdispersion - 0.001, 10, "UNDER", 2.0).zone == tight.name


def test_classify_never_recommends_fading():
    """No against-trend rule exists, by design — betting the underdog was
    worse than backing the favourite in every bucket tested. If a fade call
    ever appears it must come from a deliberate, evidenced change."""
    for overdispersion in np.linspace(1.0, 1.4, 60):
        assert classify(float(overdispersion), 10, "OVER", 1.9).call in (BACK_FAVOURITE, NO_BET)


def test_classify_handles_missing_overdispersion():
    assert classify(float("nan"), 10, "OVER", 1.9).call == NO_BET


def test_classify_skips_after_a_long_layoff():
    """v3 layoff gate: in-zone matches where either team's last finished
    match is >45 days old lost -52% ROI in the walk-forward. The gate only
    fires where the rule would otherwise bet, and an unknown gap (None)
    never blocks — the familiarity gate owns never-seen teams."""
    ideal = ZONES[0].max_overdispersion - 0.001
    assert classify(ideal, 10, "OVER", 1.8, days_since_last_match=46.0).skip_reason == "post_layoff"
    assert classify(ideal, 10, "OVER", 1.8, days_since_last_match=44.0).call == BACK_FAVOURITE
    assert classify(ideal, 10, "OVER", 1.8, days_since_last_match=None).call == BACK_FAVOURITE
    # Out of zone, the more specific reason wins — layoff is irrelevant there.
    out = classify(ZONES[1].max_overdispersion + 0.05, 10, "OVER", 1.8, days_since_last_match=90.0)
    assert out.skip_reason == "dispersion_too_high"


def test_classify_skips_unvalidated_competitions():
    """v3 competition gate: in-zone matches from competitions outside the
    validated set ran at negative ROI (true holdout, -2.75%/-3.06%). The
    default (True) preserves pre-v3 caller behavior."""
    ideal = ZONES[0].max_overdispersion - 0.001
    assert classify(ideal, 10, "OVER", 1.8, competition_validated=False).skip_reason == "unvalidated_competition"
    assert classify(ideal, 10, "OVER", 1.8, competition_validated=True).call == BACK_FAVOURITE
    assert classify(ideal, 10, "OVER", 1.8).call == BACK_FAVOURITE


def test_classify_frame_reads_gate_columns_when_present():
    from ou25_pipeline.market.decision import VALIDATED_COMPETITIONS
    validated_id = next(iter(VALIDATED_COMPETITIONS))
    ideal = ZONES[0].max_overdispersion - 0.001
    df = favourite_frame(_frame(8))
    df["overdispersion"] = ideal
    df["min_prior_matches"] = 10
    df["competition_id"] = [validated_id] * 4 + ["comp_never_validated"] * 4
    df["stale_gap"] = [10.0, 60.0, np.nan, 10.0] * 2

    out = classify_frame(df)
    # validated comp: gap decides
    assert out.loc[0, "call"] == BACK_FAVOURITE
    assert out.loc[1, "skip_reason"] == "post_layoff"
    assert out.loc[2, "call"] == BACK_FAVOURITE  # unknown gap never blocks
    # unvalidated comp: gate fires regardless of gap
    assert set(out.loc[4:, "skip_reason"]) == {"unvalidated_competition"}


def _frame(n: int = 400) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    p_over = rng.uniform(0.3, 0.7, n)
    return pd.DataFrame({
        "match_id": [f"mt_{i}" for i in range(n)],
        "kickoff_utc": pd.date_range("2024-01-01", periods=n, freq="D"),
        "quoted_prob_over_2_5": p_over,
        "goals_2_5_over_close": 1 / (p_over * 1.057),
        "goals_2_5_under_close": 1 / ((1 - p_over) * 1.057),
        "target_over_2_5": rng.random(n) < p_over,
        "overdispersion": rng.uniform(1.10, 1.22, n),
        "min_prior_matches": rng.integers(0, 12, n),
    })


def test_favourite_frame_picks_the_higher_probability_side():
    df = favourite_frame(_frame())
    assert (df["fav_prob"] >= 0.5).all()
    assert (df["fav_prob"] >= df["dog_prob"]).all()
    # Two-way market: spread is a pure restatement of favourite probability.
    assert np.allclose(df["spread"], 2 * df["fav_prob"] - 1)
    # The favourite must always be the shorter price.
    assert (df["fav_odds"] <= df["dog_odds"] + 1e-9).all()


def test_favourite_frame_marks_favourite_win_correctly():
    df = favourite_frame(_frame())
    over_fav = df[df["fav_side"] == "OVER"]
    assert (over_fav["fav_won"] == over_fav["target_over_2_5"]).all()
    under_fav = df[df["fav_side"] == "UNDER"]
    assert (under_fav["fav_won"] != under_fav["target_over_2_5"]).all()


def test_returns_pays_out_at_the_quoted_price():
    profit = returns(np.array([True, False]), np.array([2.5, 2.5]))
    assert profit[0] == pytest.approx(1.5)
    assert profit[1] == pytest.approx(-1.0)


def test_bootstrap_roi_brackets_the_point_estimate():
    rng = np.random.default_rng(1)
    profit = rng.normal(0.03, 1.0, 800)
    result = bootstrap_roi(profit, resamples=2000)
    assert result["ci_low"] < result["roi"] < result["ci_high"]
    assert 0.0 <= result["p_positive"] <= 1.0


def test_run_backtest_reports_every_zone_plus_baseline():
    df = favourite_frame(_frame())
    result = run_backtest(pd.concat([df, classify_frame(df)], axis=1))
    assert len(result["zones"]) == len(ZONES) + 1          # zones + all-matches baseline
    assert not result["sensitivity"].empty
    for zone in result["zones"]:
        assert len(zone.thirds) == 3                       # stability split always present
        assert zone.roi["ci_low"] <= zone.roi["roi"] <= zone.roi["ci_high"]


def test_build_ladder_features_keeps_row_alignment_with_unfittable_rows():
    """Rows without enough priced lines must come back as NaN in place, not
    be dropped — otherwise the caller silently misaligns match ids."""
    good = _ladder_odds(2.7)
    row_good = {f"goals_{str(l).replace('.','_')}_over_close": good[l][0] for l in LADDER_LINES}
    row_good |= {f"goals_{str(l).replace('.','_')}_under_close": good[l][1] for l in LADDER_LINES}
    row_bad = {k: np.nan for k in row_good}
    df = pd.DataFrame([row_good, row_bad, row_good])

    out = build_ladder_features(df)
    assert len(out) == 3
    assert out["overdispersion"].notna().tolist() == [True, False, True]
    assert list(out.index) == list(df.index)


def test_walk_forward_never_lets_a_block_see_its_own_data():
    """The cutoff for each test block must come only from earlier matches —
    this is the guard against the in-sample threshold optimism that the
    zone definitions would otherwise carry."""
    from ou25_pipeline.market.backtest import walk_forward

    # Large enough that each out-of-sample block still clears the
    # minimum selection size once both rule components have filtered.
    df = favourite_frame(_frame(3000))
    df["fav_won"] = df["fav_won"].astype(bool)
    result = walk_forward(df, ZONES[-1], blocks=3)

    assert result["blocks"], "expected at least one out-of-sample block"
    # Thresholds are derived from expanding train windows, so with a stable
    # distribution they should stay in the plausible overdispersion range.
    for block in result["blocks"]:
        assert 1.0 < block["threshold"] < 1.5
    assert result["pooled"]["ci_low"] <= result["pooled"]["roi"] <= result["pooled"]["ci_high"]


def test_classify_skips_when_teams_are_not_known_well_enough():
    """The shape signal firing is not sufficient — matches failing the
    familiarity filter backtested at -10.6%, so they must be skipped even
    when overdispersion looks ideal."""
    zone = ZONES[0]
    ideal_shape = zone.max_overdispersion - 0.001

    known = classify(ideal_shape, zone.min_prior_matches, "OVER", 1.8)
    assert known.call == BACK_FAVOURITE

    unknown = classify(ideal_shape, zone.min_prior_matches - 1, "OVER", 1.8)
    assert unknown.call == NO_BET
    assert unknown.skip_reason == "insufficient_team_history"


def test_classify_records_a_distinct_reason_for_each_skip():
    zone = ZONES[-1]
    assert classify(zone.max_overdispersion + 0.1, 10, "OVER", 1.8).skip_reason == "dispersion_too_high"
    assert classify(float("nan"), 10, "OVER", 1.8).skip_reason == "no_ladder_fit"
    assert classify(1.10, float("nan"), "OVER", 1.8).skip_reason == "unknown_team_history"


def test_zone_mask_requires_both_components():
    zone = ZONES[0]
    df = pd.DataFrame({
        "overdispersion": [1.10, 1.10, 1.30, 1.30],
        "min_prior_matches": [10, 0, 10, 0],
    })
    assert zone_mask(df, zone).tolist() == [True, False, False, False]


def test_prior_match_counts_are_point_in_time_and_use_the_lesser_known_team():
    """A team's count must exclude the match itself, and the pair takes the
    minimum so a long-tracked club cannot mask a newly-seen opponent."""
    df = pd.DataFrame({
        "match_id": ["m1", "m2", "m3"],
        "kickoff_utc": pd.to_datetime(["2024-01-01", "2024-01-08", "2024-01-15"]),
        "home_team_id": ["A", "A", "A"],
        "away_team_id": ["B", "B", "C"],
    })
    counts = prior_match_counts(df)
    assert counts.tolist() == [0, 1, 0]  # m3: A has 2 priors, C has 0 -> min 0
