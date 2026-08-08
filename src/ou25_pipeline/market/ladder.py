"""Reads the bookmaker's total-goals ladder as a probability distribution.

The provider prices lines 0.5-6.5 on ~92% of matches (7-9 lines each). Those
lines are not independent opinions — regressing the quoted 2.5 price on the
others gives R^2 = 0.994, so the book clearly prices the whole ladder from one
internal model. What the ladder *does* expose is the **shape** of that model's
goal distribution, and shape turns out to carry information the 2.5 price alone
does not: implied overdispersion correlates +0.008 with the implied mean and
-0.008 with the quoted 2.5 price, i.e. it is near-orthogonal to both.

Fitting is negative binomial, not Poisson. NB beats Poisson on 100% of matches
(mean SSE 0.00025 vs 0.00140), and the Poisson misfit is not harmless: it
manufactures a systematic +1.11pp bias in the curve's opinion of the 2.5 line
that collapses to -0.12pp under NB. An earlier "signal" built on Poisson
divergence was largely that artifact.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import nbinom

# Lines the provider actually prices densely enough to fit a curve through.
# 7.5+ exist but drop to 60%/14%/2% coverage, too sparse to rely on.
LADDER_LINES = (0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5)
TARGET_LINE = 2.5

_MIN_LINES_FOR_FIT = 4
_MU_BOUNDS = (0.05, 12.0)
_R_BOUNDS = (0.3, 400.0)


def devig_pair(over_odds: float, under_odds: float) -> float:
    """Overround-removed P(over) for one two-way line.

    Each line is de-vigged independently rather than against a single
    match-level overround: the book does not apply a uniform margin across
    the ladder (measured margin runs 4.2%-7.5%), so a shared adjustment
    would bake one line's margin into another's probability.
    """
    if not over_odds or not under_odds:
        return float("nan")
    implied_over, implied_under = 1.0 / over_odds, 1.0 / under_odds
    return implied_over / (implied_over + implied_under)


def survival_curve(odds_by_line: dict[float, tuple[float, float]]) -> tuple[np.ndarray, np.ndarray]:
    """(goal_counts, survival) for the lines that are priced.

    A line at L = k + 0.5 prices P(total > L), which for integer goal counts
    is exactly P(total >= k + 1) — so the ladder is a direct read of the
    survival function at integer boundaries, no interpolation needed.
    """
    counts, probs = [], []
    for line in LADDER_LINES:
        pair = odds_by_line.get(line)
        if not pair:
            continue
        p_over = devig_pair(*pair)
        if np.isnan(p_over):
            continue
        counts.append(int(line))
        probs.append(p_over)
    return np.asarray(counts, dtype=int), np.asarray(probs, dtype=float)


def _nb_survival(counts: np.ndarray, mu: float, r: float) -> np.ndarray:
    return 1.0 - nbinom.cdf(counts, r, r / (r + mu))


@dataclass(frozen=True)
class LadderFit:
    """A negative-binomial fit to one match's implied goal distribution."""

    mu: float
    r: float
    sse: float
    n_lines: int

    @property
    def implied_variance(self) -> float:
        return self.mu + self.mu**2 / self.r

    @property
    def overdispersion(self) -> float:
        """Implied variance / mean. 1.0 would be Poisson; the market prices
        ~1.16 on average, so the book is not using a naive Poisson."""
        return self.implied_variance / self.mu

    def prob_over(self, line: float) -> float:
        return float(1.0 - nbinom.cdf(int(line), self.r, self.r / (self.r + self.mu)))


def fit_ladder(
    counts: np.ndarray, survival: np.ndarray, exclude_line: float | None = TARGET_LINE
) -> LadderFit | None:
    """Fit a negative binomial to the implied survival curve.

    `exclude_line` holds one line out of the fit so the curve's opinion of
    that line stays independent of its own quoted price — necessary if you
    intend to compare the two. It defaults to the 2.5 line we bet on, which
    is also how the overdispersion signal was validated, so leave it alone
    unless you are deliberately re-validating.
    """
    if exclude_line is not None:
        keep = counts != int(exclude_line)
        counts, survival = counts[keep], survival[keep]
    if len(counts) < _MIN_LINES_FOR_FIT:
        return None

    def sse(params: np.ndarray) -> float:
        mu, r = params
        if mu <= 0 or r <= 0:
            return 1e9
        return float(np.sum((_nb_survival(counts, mu, r) - survival) ** 2))

    result = minimize(sse, x0=[2.7, 8.0], bounds=[_MU_BOUNDS, _R_BOUNDS], method="L-BFGS-B")
    if not np.isfinite(result.fun):
        return None
    mu, r = float(result.x[0]), float(result.x[1])
    return LadderFit(mu=mu, r=r, sse=float(result.fun), n_lines=len(counts))


def prior_match_counts(df: pd.DataFrame) -> pd.Series:
    """How many earlier matches we hold for the *less-known* of the two teams.

    Point-in-time: a match counts only matches strictly before its own
    kickoff. Taking the minimum across the two sides is deliberate — a
    fixture is only as well-understood as its less-established team, and
    averaging would let a long-tracked club mask a newly-promoted one.

    This drives the "skip problematic favourites" half of the rule. Matches
    failing it are not merely uninformative: backtested, the ones excluded by
    a >= 4 threshold returned -10.6% while the retained ones returned +3.1%.
    """
    ordered = df.sort_values("kickoff_utc")
    sides = pd.concat([
        ordered[["match_id", "kickoff_utc", "home_team_id"]].rename(columns={"home_team_id": "team_id"}),
        ordered[["match_id", "kickoff_utc", "away_team_id"]].rename(columns={"away_team_id": "team_id"}),
    ]).sort_values(["team_id", "kickoff_utc"])
    sides["prior"] = sides.groupby("team_id").cumcount()
    per_match = sides.groupby("match_id")["prior"].min()
    return df["match_id"].map(per_match).astype("Int64")


def _odds_columns(line: float) -> tuple[str, str]:
    tag = str(line).replace(".", "_")
    return f"goals_{tag}_over_close", f"goals_{tag}_under_close"


def build_ladder_features(df: pd.DataFrame) -> pd.DataFrame:
    """Per-match ladder features, indexed to match `df`'s row order.

    Expects the wide `goals_<line>_over_close` / `_under_close` columns as
    exported by `export/match_csv.py`. Rows without enough priced lines come
    back as NaN rather than being dropped, so the caller keeps its row
    alignment and decides what to do with them.

    This runs a two-parameter optimisation per match, so it is the slow step
    in the pipeline (order a minute for ~7.9k matches) — call it once and
    persist the result rather than recomputing per query.
    """
    records = []
    for row in df.itertuples(index=False):
        odds = {}
        for line in LADDER_LINES:
            over_col, under_col = _odds_columns(line)
            over, under = getattr(row, over_col, None), getattr(row, under_col, None)
            if over and under and not (pd.isna(over) or pd.isna(under)):
                odds[line] = (float(over), float(under))

        counts, survival = survival_curve(odds)
        fit = fit_ladder(counts, survival) if len(counts) else None
        quoted = devig_pair(*odds[TARGET_LINE]) if TARGET_LINE in odds else float("nan")

        if fit is None:
            records.append(dict(
                ladder_mu=np.nan, ladder_r=np.nan, ladder_sse=np.nan, ladder_lines=len(counts),
                implied_variance=np.nan, overdispersion=np.nan,
                curve_prob_over_2_5=np.nan, quoted_prob_over_2_5=quoted, curve_divergence=np.nan,
            ))
            continue

        curve = fit.prob_over(TARGET_LINE)
        records.append(dict(
            ladder_mu=fit.mu, ladder_r=fit.r, ladder_sse=fit.sse, ladder_lines=fit.n_lines,
            implied_variance=fit.implied_variance, overdispersion=fit.overdispersion,
            curve_prob_over_2_5=curve, quoted_prob_over_2_5=quoted,
            curve_divergence=curve - quoted,
        ))

    return pd.DataFrame.from_records(records, index=df.index)
