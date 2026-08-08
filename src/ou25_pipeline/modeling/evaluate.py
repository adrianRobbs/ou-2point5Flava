"""Evaluation harness for baseline models.

Every metric is reported next to the market's own implied probability
(`implied_prob_over_2_5`, used directly as a prediction — no model at all)
as a mandatory baseline. Raw accuracy is reported but treated as secondary:
the target is ~54/46 balanced, so a majority-class guess already scores
~54% and accuracy alone is easy to game. Beating (or even approaching) the
market on log-loss/Brier score, and finding a real edge in the confident-
disagreement subset, are the actual tests of value — see the project plan
for why an overall accuracy target isn't a meaningful goal for this market.
"""

from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from ou25_pipeline.modeling.dataset import (
    CATEGORICAL_COLUMNS,
    MARKET_PROB_COLUMN,
    TARGET_COLUMN,
    feature_columns,
)

_DISAGREEMENT_THRESHOLD = 0.07
_SUBSET_FRACTION = 0.15
_CALIBRATION_BINS = 10


def _binary_metrics(y_true: np.ndarray, y_prob: np.ndarray) -> dict:
    y_pred = (y_prob >= 0.5).astype(int)
    return {
        "log_loss": float(log_loss(y_true, y_prob, labels=[0, 1])),
        "brier_score": float(brier_score_loss(y_true, y_prob)),
        "roc_auc": float(roc_auc_score(y_true, y_prob)) if len(np.unique(y_true)) > 1 else float("nan"),
        "accuracy": float((y_pred == y_true).mean()),
        "n": int(len(y_true)),
    }


def _calibration_table(y_true: np.ndarray, y_prob: np.ndarray) -> pd.DataFrame:
    bins = pd.qcut(y_prob, _CALIBRATION_BINS, duplicates="drop")
    frame = pd.DataFrame({"bin": bins, "y_true": y_true, "y_prob": y_prob})
    return (
        frame.groupby("bin", observed=True)
        .agg(count=("y_true", "size"), mean_predicted=("y_prob", "mean"), actual_rate=("y_true", "mean"))
        .reset_index(drop=True)
    )


def _extreme_subset_hit_rate(y_true: np.ndarray, prob: np.ndarray, k: int) -> float:
    confidence = np.abs(prob - 0.5)
    idx = np.argsort(-confidence)[:k]
    pred = (prob[idx] >= 0.5).astype(int)
    return float((pred == y_true[idx]).mean())


def _subset_analysis(y_true: np.ndarray, model_prob: np.ndarray, market_prob: np.ndarray, fraction: float) -> dict:
    k = max(1, int(round(len(y_true) * fraction)))
    return {
        "subset_size": k,
        "model_hit_rate": _extreme_subset_hit_rate(y_true, model_prob, k),
        "market_hit_rate": _extreme_subset_hit_rate(y_true, market_prob, k),
    }


def _disagreement_analysis(y_true: np.ndarray, model_prob: np.ndarray, market_prob: np.ndarray, threshold: float) -> dict:
    diverges = np.abs(model_prob - market_prob) >= threshold
    n = int(diverges.sum())
    if n == 0:
        return {"subset_size": 0, "model_accuracy": None}
    pred = (model_prob[diverges] >= 0.5).astype(int)
    return {"subset_size": n, "model_accuracy": float((pred == y_true[diverges]).mean())}


def _pointwise_log_loss(y_true: np.ndarray, y_prob: np.ndarray) -> np.ndarray:
    p = np.clip(y_prob, 1e-9, 1 - 1e-9)
    return -(y_true * np.log(p) + (1 - y_true) * np.log(1 - p))


def _significance_vs_market(y_true: np.ndarray, model_prob: np.ndarray, market_prob: np.ndarray) -> dict:
    """Is the model-vs-market difference real, or noise?

    Both tests are *paired* — comparing the two predictions match by match
    rather than comparing two aggregate numbers — which is far more
    sensitive, since the two predictors face exactly the same fixtures and
    most of the variance is the shared difficulty of the matches themselves.

    Without this, small differences in the headline table are easy to
    over-read: at n≈1500 an accuracy gap of ~1pp is well inside one standard
    error, so it means nothing on its own.
    """
    model_losses = _pointwise_log_loss(y_true, model_prob)
    market_losses = _pointwise_log_loss(y_true, market_prob)
    _, log_loss_p = stats.ttest_rel(model_losses, market_losses)

    model_right = (model_prob >= 0.5).astype(int) == y_true
    market_right = (market_prob >= 0.5).astype(int) == y_true
    model_only = int((model_right & ~market_right).sum())
    market_only = int((~model_right & market_right).sum())
    # McNemar: among matches where exactly one of the two was right, is the
    # split meaningfully away from 50/50? Matches both got right (or both
    # wrong) carry no information about which predictor is better.
    discordant = model_only + market_only
    accuracy_p = float(stats.binomtest(model_only, discordant, 0.5).pvalue) if discordant else float("nan")

    return {
        "mean_log_loss_delta": float((model_losses - market_losses).mean()),
        "log_loss_p_value": float(log_loss_p),
        "accuracy_p_value": accuracy_p,
        "model_only_correct": model_only,
        "market_only_correct": market_only,
    }


def _introspectable(model):
    """Reach the preprocessor+estimator pair through any wrappers.

    `CalibratedClassifierCV` hides the real model two levels down (a
    per-fold calibrated classifier, then the frozen inner estimator), and
    the importances belong to that inner model — the calibrator only
    reshapes its output probabilities, it has no features of its own.
    Returns None when there's nothing recognisable to introspect.
    """
    if hasattr(model, "calibrated_classifiers_"):
        inner = model.calibrated_classifiers_[0].estimator
        model = getattr(inner, "estimator", inner)  # unwrap FrozenEstimator
    return model if hasattr(model, "named_steps") else None


def _feature_importances(model, df: pd.DataFrame) -> pd.DataFrame:
    model = _introspectable(model)
    if model is None:
        return pd.DataFrame(columns=["feature", "importance"])

    preprocessor = model.named_steps["preprocess"]
    classifier = model.named_steps["classify"]
    cat_encoder = preprocessor.named_transformers_["categorical"]
    names = feature_columns(df) + list(cat_encoder.get_feature_names_out(CATEGORICAL_COLUMNS))

    if hasattr(classifier, "feature_importances_"):
        importances = classifier.feature_importances_
    elif hasattr(classifier, "coef_"):
        importances = np.abs(classifier.coef_[0])
    else:
        return pd.DataFrame(columns=["feature", "importance"])

    return (
        pd.DataFrame({"feature": names, "importance": importances})
        .sort_values("importance", ascending=False)
        .reset_index(drop=True)
    )


def evaluate(model, test_df: pd.DataFrame) -> dict:
    y_true = test_df[TARGET_COLUMN].astype(int).to_numpy()
    model_prob = model.predict_proba(test_df)[:, 1]
    market_prob = test_df[MARKET_PROB_COLUMN].to_numpy()

    # Head-to-head metrics are restricted to matches that actually have a
    # market price, so both sides are scored on an identical row set. The
    # model can predict every match; the market can't be quoted where the
    # bookmaker never offered a total-goals line (a real coverage gap for
    # Eredivisie/Brasileirão), and scoring the model on extra matches the
    # market never saw would not be a like-for-like comparison.
    priced = ~np.isnan(market_prob)

    return {
        "model_metrics": _binary_metrics(y_true[priced], model_prob[priced]),
        "market_metrics": _binary_metrics(y_true[priced], market_prob[priced]),
        "model_metrics_all_matches": _binary_metrics(y_true, model_prob),
        "significance": _significance_vs_market(y_true[priced], model_prob[priced], market_prob[priced]),
        "calibration_table": _calibration_table(y_true, model_prob),
        "subset_analysis": _subset_analysis(y_true[priced], model_prob[priced], market_prob[priced], _SUBSET_FRACTION),
        "disagreement_analysis": _disagreement_analysis(
            y_true[priced], model_prob[priced], market_prob[priced], _DISAGREEMENT_THRESHOLD
        ),
        "feature_importances": _feature_importances(model, test_df),
    }


def write_report(results_by_model: dict[str, dict], output_path: Path) -> None:
    lines = [
        "# Baseline model evaluation report",
        "",
        "Market baseline throughout is `implied_prob_over_2_5` used directly as the "
        "prediction, with no model at all. A model log-loss *dramatically* better than "
        "the market's is a leakage red flag to investigate, not a win to report.",
        "",
    ]

    for model_name, results in results_by_model.items():
        lines.append(f"## {model_name}")
        lines.append("")
        lines.append("Both columns scored on the same matches — those that have a market price.")
        lines.append("")
        lines.append("| metric | model | market | model better? |")
        lines.append("|---|---|---|---|")
        for key in ["log_loss", "brier_score", "roc_auc", "accuracy"]:
            model_value = results["model_metrics"][key]
            market_value = results["market_metrics"][key]
            lower_is_better = key in ("log_loss", "brier_score")
            better = model_value < market_value if lower_is_better else model_value > market_value
            lines.append(f"| {key} | {model_value:.4f} | {market_value:.4f} | {'yes' if better else 'no'} |")
        lines.append(f"| n | {results['model_metrics']['n']} | {results['market_metrics']['n']} | — |")
        lines.append("")
        lines.append(
            f"Model on all {results['model_metrics_all_matches']['n']} test matches including unpriced ones: "
            f"log_loss {results['model_metrics_all_matches']['log_loss']:.4f}, "
            f"accuracy {results['model_metrics_all_matches']['accuracy']:.4f}."
        )
        lines.append("")

        sig = results["significance"]
        lines.append("### Is the difference from the market real?")
        lines.append("")
        lines.append(
            f"- mean per-match log-loss delta: {sig['mean_log_loss_delta']:+.4f} "
            "(negative = model better)"
        )
        lines.append(f"- paired t-test on per-match log-loss, p = {sig['log_loss_p_value']:.3f}")
        lines.append(
            f"- McNemar test on accuracy, p = {sig['accuracy_p_value']:.3f} "
            f"(model-only-correct {sig['model_only_correct']}, market-only-correct {sig['market_only_correct']})"
        )
        lines.append(
            "- A p-value above ~0.05 means the gap in the table above is not distinguishable "
            "from noise, in *either* direction — read it as a tie, not as a win or a loss."
        )
        lines.append("")

        lines.append("### Calibration (deciles of model predicted probability)")
        lines.append("")
        lines.append("| count | mean predicted | actual rate |")
        lines.append("|---|---|---|")
        for _, row in results["calibration_table"].iterrows():
            lines.append(f"| {int(row['count'])} | {row['mean_predicted']:.3f} | {row['actual_rate']:.3f} |")
        lines.append("")

        subset = results["subset_analysis"]
        lines.append(f"### High-confidence subset (top {int(_SUBSET_FRACTION * 100)}% most confident predictions)")
        lines.append("")
        lines.append(f"- subset size: {subset['subset_size']}")
        lines.append(f"- model hit rate: {subset['model_hit_rate']:.3f}")
        lines.append(f"- market hit rate (same-sized subset by market confidence): {subset['market_hit_rate']:.3f}")
        lines.append("")

        disagreement = results["disagreement_analysis"]
        lines.append(f"### Disagreement subset (|model_prob - market_prob| >= {_DISAGREEMENT_THRESHOLD})")
        lines.append("")
        lines.append(f"- subset size: {disagreement['subset_size']}")
        acc = disagreement["model_accuracy"]
        lines.append(
            f"- model accuracy on disagreement subset: {acc:.3f}" if acc is not None else "- no disagreement above threshold"
        )
        lines.append("")

        lines.append("### Top 15 feature importances")
        lines.append("")
        lines.append("| feature | importance |")
        lines.append("|---|---|")
        for _, row in results["feature_importances"].head(15).iterrows():
            lines.append(f"| {row['feature']} | {row['importance']:.4f} |")
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
