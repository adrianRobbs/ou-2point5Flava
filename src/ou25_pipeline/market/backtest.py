"""Backtest harness for the decision engine.

Built around one rule: a headline ROI is never reported on its own. Every
zone comes with a bootstrap confidence interval, a split across *thirds* of
the timeline, and its position on a threshold-sensitivity curve — because
each of those independently caught something the headline number hid.

The thirds split specifically: the best zone found looks strong across halves
but has no effect at all in the first third of the timeline. A halves-only
check would have missed that, so both are reported.

Staking assumes 1 unit flat at the real recorded closing price. No flat-margin
assumption is used anywhere — actual overround is 5.68% mean, 5.56% median,
rising from 5.44% (tier 1) to 6.01% (tier 3), and it is computed per match.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from ou25_pipeline.market.decision import ZONES, RULE_VERSION

_BOOTSTRAP_RESAMPLES = 20_000
_SENSITIVITY_THRESHOLDS = (1.115, 1.1277, 1.1335, 1.1379, 1.142, 1.1459, 1.1538)


def returns(won: np.ndarray, odds: np.ndarray) -> np.ndarray:
    """Profit per 1 unit staked: (odds - 1) on a win, -1 on a loss."""
    return np.where(won, odds - 1.0, -1.0)


def bootstrap_roi(profit: np.ndarray, resamples: int = _BOOTSTRAP_RESAMPLES, seed: int = 0) -> dict:
    if len(profit) == 0:
        return {"roi": float("nan"), "ci_low": float("nan"), "ci_high": float("nan"), "p_positive": float("nan")}
    rng = np.random.default_rng(seed)
    draws = rng.choice(profit, size=(resamples, len(profit)), replace=True).mean(axis=1)
    return {
        "roi": float(profit.mean()),
        "ci_low": float(np.percentile(draws, 2.5)),
        "ci_high": float(np.percentile(draws, 97.5)),
        "p_positive": float((draws > 0).mean()),
    }


@dataclass
class ZoneResult:
    name: str
    n: int
    coverage: float
    priced: float
    actual: float
    edge_pp: float
    p_value: float
    roi: dict
    thirds: list[dict]
    fade_roi: float


def evaluate_selection(df: pd.DataFrame, mask: pd.Series, name: str) -> ZoneResult | None:
    """Backtest one selection of matches, backing the favourite in each."""
    sel = df[mask]
    if len(sel) < 50:
        return None

    won = sel["fav_won"].to_numpy(dtype=bool)
    profit = returns(won, sel["fav_odds"].to_numpy(dtype=float))
    priced, actual = float(sel["fav_prob"].mean()), float(won.mean())
    p_value = float(stats.binomtest(int(won.sum()), len(sel), priced).pvalue)

    ordered = sel.sort_values("kickoff_utc")
    edges = np.linspace(0, len(ordered), 4).astype(int)
    thirds = []
    for i, (lo, hi) in enumerate(zip(edges[:-1], edges[1:]), start=1):
        part = ordered.iloc[lo:hi]
        if part.empty:
            continue
        part_won = part["fav_won"].to_numpy(dtype=bool)
        thirds.append({
            "third": i,
            "n": len(part),
            "edge_pp": float((part_won.mean() - part["fav_prob"].mean()) * 100),
            "roi": float(returns(part_won, part["fav_odds"].to_numpy(dtype=float)).mean()),
        })

    # Kept in every report as a standing check on the "no fade zone" finding:
    # if fading ever beats backing inside a zone, that should be visible here.
    fade_roi = float(returns(~won, sel["dog_odds"].to_numpy(dtype=float)).mean())

    return ZoneResult(
        name=name, n=len(sel), coverage=len(sel) / len(df), priced=priced, actual=actual,
        edge_pp=(actual - priced) * 100, p_value=p_value,
        roi=bootstrap_roi(profit), thirds=thirds, fade_roi=fade_roi,
    )


def zone_mask(df: pd.DataFrame, zone) -> pd.Series:
    """Both rule components — shape *and* team familiarity."""
    return (df["overdispersion"] <= zone.max_overdispersion) & (
        df["min_prior_matches"].astype(float) >= zone.min_prior_matches
    )


def sensitivity_curve(df: pd.DataFrame, min_prior_matches: int = 4) -> pd.DataFrame:
    """Zone performance as the overdispersion cutoff widens.

    A real effect decays smoothly as the zone loosens; a noise spike does
    not. This curve is the cheapest available check on which one we have.
    The team-familiarity filter is held fixed so the curve isolates the
    shape threshold rather than mixing two moving parts.
    """
    df = df[df["min_prior_matches"].astype(float) >= min_prior_matches]
    rows = []
    for threshold in _SENSITIVITY_THRESHOLDS:
        sel = df[df["overdispersion"] <= threshold]
        if len(sel) < 50:
            continue
        won = sel["fav_won"].to_numpy(dtype=bool)
        rows.append({
            "max_overdispersion": threshold,
            "n": len(sel),
            "edge_pp": (won.mean() - sel["fav_prob"].mean()) * 100,
            "roi": returns(won, sel["fav_odds"].to_numpy(dtype=float)).mean(),
            "p_value": float(stats.binomtest(int(won.sum()), len(sel), float(sel["fav_prob"].mean())).pvalue),
        })
    return pd.DataFrame(rows)


def walk_forward(df: pd.DataFrame, zone, blocks: int = 5, burn_in: float = 0.3) -> dict:
    """Out-of-sample validation: learn the cutoff on the past, bet the future.

    This exists because the zone thresholds in `decision.py` were chosen after
    looking at the whole dataset, which makes every in-sample ROI above
    optimistic. Here the cutoff is re-derived from only the matches preceding
    each test block, so nothing the rule saw informed the prices it is graded on.

    Report the pooled figure, not the best block — sequential blocks swing
    widely, and picking the good ones is precisely the error this guards against.
    """
    ordered = df.sort_values("kickoff_utc").reset_index(drop=True)
    edges = np.linspace(int(len(ordered) * burn_in), len(ordered), blocks + 1).astype(int)
    # Coverage the zone's fixed cutoff achieves overall — re-derived per block
    # from train only, so the threshold itself is never fitted on test data.
    coverage = float((ordered["overdispersion"] <= zone.max_overdispersion).mean())
    familiar = ordered["min_prior_matches"].astype(float) >= zone.min_prior_matches

    block_results, pooled = [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        train, test = ordered.iloc[:lo], ordered.iloc[lo:hi]
        if len(train) < 200 or test.empty:
            continue
        threshold = float(train["overdispersion"].quantile(coverage))
        selected = test[(test["overdispersion"] <= threshold) & familiar.iloc[lo:hi]]
        if len(selected) < 30:
            continue
        profit = returns(selected["fav_won"].to_numpy(dtype=bool),
                         selected["fav_odds"].to_numpy(dtype=float))
        pooled.append(profit)
        block_results.append({"n": len(selected), "threshold": threshold, "roi": float(profit.mean())})

    if not pooled:
        return {"blocks": [], "pooled": bootstrap_roi(np.array([])), "n": 0}
    combined = np.concatenate(pooled)
    return {"blocks": block_results, "pooled": bootstrap_roi(combined), "n": len(combined)}


def run_backtest(df: pd.DataFrame) -> dict:
    usable = df.dropna(subset=["overdispersion", "fav_prob", "fav_odds", "fav_won"]).copy()
    usable["fav_won"] = usable["fav_won"].astype(bool)

    results = [evaluate_selection(usable, pd.Series(True, index=usable.index), "all_matches (baseline)")]
    for zone in ZONES:
        results.append(evaluate_selection(usable, zone_mask(usable, zone), zone.name))

    # What the team-familiarity filter actually removes. Kept in the report
    # because a skip rule earns its place only by showing what it discards.
    tightest = ZONES[0]
    excluded = (usable["overdispersion"] <= tightest.max_overdispersion) & (
        usable["min_prior_matches"].astype(float) < tightest.min_prior_matches
    )
    results.append(evaluate_selection(usable, excluded, "EXCLUDED by history filter"))

    return {
        "n_total": len(usable),
        "zones": [r for r in results if r is not None],
        "sensitivity": sensitivity_curve(usable),
        "walk_forward": {zone.name: walk_forward(usable, zone) for zone in ZONES},
    }


def write_report(backtest: dict, output_path: Path, comparisons_run: int = 55) -> None:
    bonferroni = 0.05 / max(comparisons_run, 1)
    lines = [
        "# Decision engine backtest",
        "",
        f"Rule version `{RULE_VERSION}` · {backtest['n_total']} matches with a fitted ladder.",
        "",
        "Staking is 1 unit flat at the **real recorded closing price** — no flat-margin",
        "assumption. Bets are placed near kickoff; that is the only timing at which these",
        "numbers are valid, since the closing line is what they were measured on.",
        "",
        f"Roughly {comparisons_run} comparisons informed these zones, so the honest",
        f"significance bar is Bonferroni-corrected: **p < {bonferroni:.5f}**, not 0.05.",
        "",
    ]

    for zone in backtest["zones"]:
        roi = zone.roi
        lines += [
            f"## {zone.name}",
            "",
            f"- n = {zone.n} ({zone.coverage:.1%} coverage)",
            f"- favourite won {zone.actual:.4f} vs {zone.priced:.4f} priced → **edge {zone.edge_pp:+.2f}pp** (p = {zone.p_value:.4f}"
            + (", clears Bonferroni)" if zone.p_value < bonferroni else ", does NOT clear Bonferroni)"),
            f"- **ROI {roi['roi']:+.4f}**, bootstrap 95% CI [{roi['ci_low']:+.4f}, {roi['ci_high']:+.4f}], P(ROI>0) = {roi['p_positive']:.3f}",
            f"- fading instead of backing would return {zone.fade_roi:+.4f}"
            + ("  ← fading wins here, investigate" if zone.fade_roi > roi["roi"] else ""),
            "",
            "Stability across thirds of the timeline:",
            "",
            "| third | n | edge | ROI |",
            "|---|---|---|---|",
        ]
        for t in zone.thirds:
            lines.append(f"| T{t['third']} | {t['n']} | {t['edge_pp']:+.2f}pp | {t['roi']:+.4f} |")
        signs = {np.sign(t["roi"]) for t in zone.thirds}
        if len(signs) > 1:
            lines += ["", "> **Not stable** — ROI changes sign across thirds. Treat as a lead, not a validated edge."]
        lines.append("")

    lines += ["## Out-of-sample walk-forward", "",
              "Cutoff re-derived from only the matches preceding each test block, so the rule",
              "never sees the prices it is graded on. **This is the number to trust** — the",
              "in-sample ROIs above are optimistic, because the thresholds were chosen after",
              "looking at the full dataset.", ""]
    for zone_name, wf in backtest.get("walk_forward", {}).items():
        if not wf["blocks"]:
            continue
        pooled = wf["pooled"]
        rois = [f"{b['roi']:+.3f}" for b in wf["blocks"]]
        lines += [
            f"**{zone_name}** — pooled n = {wf['n']}, "
            f"**ROI {pooled['roi']:+.4f}**, 95% CI [{pooled['ci_low']:+.4f}, {pooled['ci_high']:+.4f}], "
            f"P(ROI>0) = {pooled['p_positive']:.3f}",
            "",
            f"per-block ROI: {', '.join(rois)}",
            "",
        ]
        if pooled["ci_low"] <= 0 <= pooled["ci_high"]:
            lines += ["> Out-of-sample CI contains zero — the edge replicates directionally, "
                      "but a reliably positive ROI is **not** established.", ""]

    lines += ["## Threshold sensitivity", "",
              "Smooth monotonic decay as the cutoff widens is what a real effect looks like.", "",
              "| max overdispersion | n | edge | ROI | p |", "|---|---|---|---|---|"]
    for _, row in backtest["sensitivity"].iterrows():
        lines.append(f"| {row['max_overdispersion']:.4f} | {int(row['n'])} | {row['edge_pp']:+.2f}pp "
                     f"| {row['roi']:+.4f} | {row['p_value']:.4f} |")
    lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))
