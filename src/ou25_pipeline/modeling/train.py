"""Model trainers.

Four variants, forming an ablation rather than four independent guesses —
each later one targets a specific weakness the evaluation report exposed in
the plain baselines:

- `train_logistic_regression` / `train_xgboost`: the plain baselines.
- `train_calibrated_xgboost`: the baselines' calibration tables showed clear
  overconfidence (top decile predicted ~0.80, actually hit ~0.70), which is
  paid for directly in log-loss. Fits a calibrator on a held-out chronological
  slice of the training data.
- `MarketAnchoredXGBoost`: the plain baselines are handed the market's
  probability as one feature among ~60 and must re-derive its mapping through
  splits/coefficients, which they do imperfectly — hence scoring worse than
  simply quoting the market back. This variant starts from the market's own
  log-odds and learns only the residual correction to it.

Models are returned as fitted, `predict_proba`-capable estimators taking the
raw feature DataFrame (preprocessing included), so evaluation and persistence
are uniform across variants.
"""

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.frozen import FrozenEstimator
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from xgboost import XGBClassifier

from ou25_pipeline.modeling.dataset import (
    CATEGORICAL_COLUMNS,
    MARKET_PROB_COLUMN,
    TARGET_COLUMN,
    chronological_split,
    feature_columns,
)

_CALIBRATION_HOLDOUT_FRAC = 0.2


def _build_preprocessor(df: pd.DataFrame, impute_scale_numeric: bool) -> ColumnTransformer:
    numeric_cols = feature_columns(df)
    if impute_scale_numeric:
        # Logistic regression can't take NaN — impute with the training
        # median, then scale so the L2 penalty treats features comparably.
        numeric_transformer = Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ])
    else:
        # XGBoost splits on missingness natively — a missing
        # h2h_over_2_5_rate ("first-ever meeting") is itself informative,
        # so leave NaN alone rather than imputing it away.
        numeric_transformer = "passthrough"

    return ColumnTransformer([
        ("numeric", numeric_transformer, numeric_cols),
        ("categorical", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_COLUMNS),
    ])


def train_logistic_regression(train_df: pd.DataFrame) -> Pipeline:
    pipeline = Pipeline([
        ("preprocess", _build_preprocessor(train_df, impute_scale_numeric=True)),
        ("classify", LogisticRegression(max_iter=1000)),
    ])
    pipeline.fit(train_df, train_df[TARGET_COLUMN])
    return pipeline


def train_xgboost(train_df: pd.DataFrame) -> Pipeline:
    pipeline = Pipeline([
        ("preprocess", _build_preprocessor(train_df, impute_scale_numeric=False)),
        ("classify", XGBClassifier(
            n_estimators=300,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="logloss",
        )),
    ])
    pipeline.fit(train_df, train_df[TARGET_COLUMN])
    return pipeline


def train_calibrated_xgboost(train_df: pd.DataFrame, method: str = "sigmoid") -> CalibratedClassifierCV:
    """XGBoost with its probabilities recalibrated on a held-out slice.

    The holdout is carved off chronologically (the most recent fifth of the
    training data), not by random CV folds — random folds would let the
    calibrator see matches that postdate the ones the model trained on,
    reintroducing exactly the leakage the top-level split avoids.

    Sigmoid (Platt) rather than isotonic by default: the calibration holdout
    is only ~1.2k rows, where isotonic's step function overfits readily, and
    the miscalibration seen in the baselines is a smooth shrink-toward-the-
    base-rate that a sigmoid captures well.
    """
    core_df, calibration_df = chronological_split(train_df, test_frac=_CALIBRATION_HOLDOUT_FRAC)
    base = train_xgboost(core_df)
    calibrated = CalibratedClassifierCV(FrozenEstimator(base), method=method)
    calibrated.fit(calibration_df, calibration_df[TARGET_COLUMN])
    return calibrated


class MarketAnchoredXGBoost(ClassifierMixin, BaseEstimator):
    """XGBoost trained with the market's own log-odds as `base_margin`.

    `base_margin` is an per-row additive offset in log-odds space that
    XGBoost treats as the starting point for boosting. Setting it to the
    market's implied log-odds means the trees only ever model the *residual*
    — "where and by how much is the market wrong" — instead of having to
    reconstruct the market's own pricing from scratch before they can
    improve on it.

    The trees are deliberately shallow, slow, and heavily regularised
    (`min_child_weight`, `reg_lambda`): against an efficiently-priced market
    the true residual is small, so anything that looks like a large
    correction is far more likely noise than edge. Without that constraint
    the model has enough capacity to wander well away from the anchor by
    fitting noise, which defeats the point of anchoring in the first place.

    Matches with no market price (a real gap for Eredivisie/Brasileirão,
    where the bookmaker often never offered a total-goals market) anchor to
    the training base rate rather than to 0.5, so an absent price doesn't
    silently become a confident "coin flip" claim.
    """

    def __init__(
        self,
        n_estimators: int = 150,
        max_depth: int = 2,
        learning_rate: float = 0.02,
        min_child_weight: float = 20.0,
        reg_lambda: float = 5.0,
    ):
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.min_child_weight = min_child_weight
        self.reg_lambda = reg_lambda

    def _market_margin(self, df: pd.DataFrame) -> np.ndarray:
        prob = df[MARKET_PROB_COLUMN].to_numpy(dtype=float)
        prob = np.where(np.isnan(prob), self.fallback_prob_, prob)
        prob = np.clip(prob, 1e-6, 1 - 1e-6)
        return np.log(prob / (1 - prob))

    def fit(self, df: pd.DataFrame, y: pd.Series) -> "MarketAnchoredXGBoost":
        y = np.asarray(y).astype(int)
        self.classes_ = np.array([0, 1])
        self.fallback_prob_ = float(y.mean())

        self.preprocessor_ = _build_preprocessor(df, impute_scale_numeric=False)
        features = self.preprocessor_.fit_transform(df)
        self.booster_ = XGBClassifier(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            min_child_weight=self.min_child_weight,
            reg_lambda=self.reg_lambda,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="logloss",
        )
        self.booster_.fit(features, y, base_margin=self._market_margin(df))
        return self

    def predict_proba(self, df: pd.DataFrame) -> np.ndarray:
        features = self.preprocessor_.transform(df)
        return self.booster_.predict_proba(features, base_margin=self._market_margin(df))

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return (self.predict_proba(df)[:, 1] >= 0.5).astype(int)

    @property
    def named_steps(self) -> dict:
        """Mirrors the `Pipeline` attribute so evaluation can introspect the
        preprocessor and estimator the same way for every model variant."""
        return {"preprocess": self.preprocessor_, "classify": self.booster_}


def train_market_anchored_xgboost(train_df: pd.DataFrame) -> MarketAnchoredXGBoost:
    model = MarketAnchoredXGBoost()
    model.fit(train_df, train_df[TARGET_COLUMN])
    return model


def save_model(model, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)


def load_model(path: Path):
    return joblib.load(path)
