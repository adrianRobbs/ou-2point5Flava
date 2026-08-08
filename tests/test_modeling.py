import numpy as np
import pandas as pd
from sklearn.metrics import log_loss

from ou25_pipeline.modeling.dataset import chronological_split
from ou25_pipeline.modeling.evaluate import evaluate
from ou25_pipeline.modeling.train import (
    load_model,
    save_model,
    train_calibrated_xgboost,
    train_logistic_regression,
    train_market_anchored_xgboost,
    train_xgboost,
)

ALL_TRAINERS = [
    train_logistic_regression,
    train_xgboost,
    train_calibrated_xgboost,
    train_market_anchored_xgboost,
]


def _make_synthetic_df(n_per_competition: int = 60, seed: int = 0) -> pd.DataFrame:
    """A minimal stand-in for `matches_features.csv`, shaped closely enough
    (same column *roles* — id/categorical/target/numeric-feature, including
    `implied_prob_over_2_5` as both a feature and the market baseline) that
    the dataset/train/evaluate functions exercise the same code paths they
    do against the real export, without needing a database."""
    rng = np.random.default_rng(seed)
    frames = []
    for comp_idx, comp_id in enumerate(["comp_A", "comp_B"]):
        n = n_per_competition
        kickoffs = pd.date_range("2024-01-01", periods=n, freq="3D") + pd.Timedelta(days=comp_idx)
        home_goals = rng.integers(0, 4, n)
        away_goals = rng.integers(0, 4, n)
        total_goals = home_goals + away_goals
        market_prob = np.clip(rng.normal(0.55, 0.12, n), 0.05, 0.95)
        frame = pd.DataFrame({
            "match_id": [f"{comp_id}_{i}" for i in range(n)],
            "competition_id": comp_id,
            "competition_name": comp_id,
            "season_id": "season_1",
            "season_name": "2024",
            "kickoff_utc": kickoffs,
            "home_team_id": [f"team_{comp_id}_{i % 6}" for i in range(n)],
            "away_team_id": [f"team_{comp_id}_{(i + 3) % 6}" for i in range(n)],
            "home_goals_ft": home_goals,
            "away_goals_ft": away_goals,
            "total_goals_ft": total_goals,
            "target_over_2_5": total_goals > 2,
            "tier": 1 if comp_id == "comp_A" else 2,
            "home_last3_gf": rng.normal(4, 1.5, n),
            "away_last3_gf": rng.normal(4, 1.5, n),
            "home_decay_weighted_gf": rng.normal(3, 1, n),
            "away_decay_weighted_gf": rng.normal(3, 1, n),
            "implied_prob_over_2_5": market_prob,
            "implied_prob_under_2_5": 1 - market_prob,
            "h2h_matches_played": rng.integers(0, 5, n),
            "h2h_over_2_5_rate": rng.uniform(0, 1, n),
        })
        # A few NaNs, mimicking early-history rows in the real export.
        frame.loc[frame.index[:3], "home_last3_gf"] = np.nan
        frame.loc[frame.index[:3], "h2h_over_2_5_rate"] = np.nan
        frames.append(frame)
    return pd.concat(frames).reset_index(drop=True)


def test_chronological_split_respects_order_per_competition():
    df = _make_synthetic_df()
    train, test = chronological_split(df, test_frac=0.2)

    assert set(train["match_id"]) & set(test["match_id"]) == set()
    assert len(train) + len(test) == len(df)

    for competition_id in df["competition_id"].unique():
        train_kickoffs = train.loc[train["competition_id"] == competition_id, "kickoff_utc"]
        test_kickoffs = test.loc[test["competition_id"] == competition_id, "kickoff_utc"]
        assert train_kickoffs.max() < test_kickoffs.min()


def test_evaluate_market_baseline_log_loss_matches_hand_computed():
    train_df = _make_synthetic_df(n_per_competition=60, seed=1)
    pipeline = train_logistic_regression(train_df)

    test_df = _make_synthetic_df(n_per_competition=4, seed=2)
    test_df = test_df[test_df["competition_id"] == "comp_A"].reset_index(drop=True)
    test_df["target_over_2_5"] = [True, False, True, False]
    test_df["implied_prob_over_2_5"] = [0.8, 0.3, 0.6, 0.4]
    test_df["implied_prob_under_2_5"] = 1 - test_df["implied_prob_over_2_5"]

    expected = log_loss([1, 0, 1, 0], [0.8, 0.3, 0.6, 0.4], labels=[0, 1])

    result = evaluate(pipeline, test_df)
    assert abs(result["market_metrics"]["log_loss"] - expected) < 1e-9


def test_evaluate_calibration_table_bin_counts_sum_to_total():
    df = _make_synthetic_df(n_per_competition=100, seed=3)
    train_df, test_df = chronological_split(df, test_frac=0.2)
    pipeline = train_xgboost(train_df)

    result = evaluate(pipeline, test_df)
    assert result["calibration_table"]["count"].sum() == len(test_df)


def test_train_produces_valid_probabilities_for_every_model_type():
    df = _make_synthetic_df(n_per_competition=80, seed=4)
    train_df, test_df = chronological_split(df, test_frac=0.2)

    for trainer in ALL_TRAINERS:
        model = trainer(train_df)
        probs = model.predict_proba(test_df)[:, 1]
        assert not np.isnan(probs).any(), trainer.__name__
        assert probs.min() >= 0.0, trainer.__name__
        assert probs.max() <= 1.0, trainer.__name__


def test_evaluate_scores_model_and_market_on_identical_row_sets():
    df = _make_synthetic_df(n_per_competition=80, seed=6)
    train_df, test_df = chronological_split(df, test_frac=0.2)
    # Some matches genuinely have no market price (the bookmaker never
    # offered a total-goals line) — the head-to-head comparison must drop
    # those from both sides rather than scoring the model on extra rows.
    test_df.loc[test_df.index[:5], "implied_prob_over_2_5"] = np.nan
    model = train_xgboost(train_df)

    result = evaluate(model, test_df)
    assert result["model_metrics"]["n"] == result["market_metrics"]["n"]
    assert result["model_metrics"]["n"] == len(test_df) - 5
    assert result["model_metrics_all_matches"]["n"] == len(test_df)


def test_market_anchored_model_stays_closer_to_market_than_unanchored():
    """The anchoring mechanism's whole job is to keep predictions tied to
    the market and learn only a residual. Asserting that relative to an
    unanchored model on identical data tests that mechanism directly —
    an absolute distance threshold would mostly encode the noise level of
    the synthetic fixture rather than anything about anchoring."""
    rng = np.random.default_rng(7)
    df = _make_synthetic_df(n_per_competition=150, seed=7)
    for column in ["home_last3_gf", "away_last3_gf", "home_decay_weighted_gf", "away_decay_weighted_gf"]:
        df[column] = rng.normal(0, 1, len(df))
    train_df, test_df = chronological_split(df, test_frac=0.2)
    market = test_df["implied_prob_over_2_5"].to_numpy()

    anchored = train_market_anchored_xgboost(train_df).predict_proba(test_df)[:, 1]
    unanchored = train_xgboost(train_df).predict_proba(test_df)[:, 1]

    assert np.abs(anchored - market).mean() < np.abs(unanchored - market).mean()


def test_market_anchored_model_handles_missing_market_price():
    df = _make_synthetic_df(n_per_competition=80, seed=8)
    train_df, test_df = chronological_split(df, test_frac=0.2)
    train_df.loc[train_df.index[:10], "implied_prob_over_2_5"] = np.nan
    test_df.loc[test_df.index[:5], "implied_prob_over_2_5"] = np.nan

    model = train_market_anchored_xgboost(train_df)
    probs = model.predict_proba(test_df)[:, 1]
    assert not np.isnan(probs).any()


def test_significance_reports_a_tie_when_model_merely_copies_the_market():
    """A model whose predictions *are* the market's must come out as an
    exact tie — zero log-loss delta, no discordant predictions. Guards the
    significance test against reporting a spurious edge from noise alone."""
    df = _make_synthetic_df(n_per_competition=80, seed=10)
    _, test_df = chronological_split(df, test_frac=0.2)

    class MarketParrot:
        def predict_proba(self, frame):
            p = frame["implied_prob_over_2_5"].to_numpy(dtype=float)
            return np.column_stack([1 - p, p])

    result = evaluate(MarketParrot(), test_df)
    sig = result["significance"]
    assert abs(sig["mean_log_loss_delta"]) < 1e-12
    assert sig["model_only_correct"] == 0
    assert sig["market_only_correct"] == 0
    assert result["feature_importances"].empty  # nothing introspectable


def test_evaluate_reports_feature_importances_through_calibration_wrapper():
    df = _make_synthetic_df(n_per_competition=120, seed=9)
    train_df, test_df = chronological_split(df, test_frac=0.2)
    model = train_calibrated_xgboost(train_df)

    result = evaluate(model, test_df)
    # The calibrator has no features of its own; importances must come from
    # the wrapped inner model rather than silently returning empty.
    assert not result["feature_importances"].empty


def test_save_and_load_model_round_trip(tmp_path):
    df = _make_synthetic_df(n_per_competition=60, seed=5)
    train_df, test_df = chronological_split(df, test_frac=0.2)
    pipeline = train_logistic_regression(train_df)

    model_path = tmp_path / "model.joblib"
    save_model(pipeline, model_path)
    loaded = load_model(model_path)

    original_probs = pipeline.predict_proba(test_df)[:, 1]
    loaded_probs = loaded.predict_proba(test_df)[:, 1]
    assert np.allclose(original_probs, loaded_probs)
