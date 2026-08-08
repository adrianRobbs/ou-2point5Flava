"""The with/against-trend decision engine.

Framing: the market names a favourite (the higher de-vigged side of the 2.5
line). Default is to side with it; the job is to find where that is worth
paying the margin for, and where to stand aside.

The rule has **two** components, and that number is a finding, not a
placeholder:

1. `overdispersion <= threshold` — the market implies a tight goal
   distribution. Teams' realised match totals are *less* volatile (~0.90)
   than the market prices (~1.16), so the book systematically over-prices
   goal variance; the favourite beats its own price where it does so least.
2. `min_prior_matches >= threshold` — we have seen both teams enough times.
   This is the "skip problematic favourites" half. On the 7,864-match
   backtest, matches failing it returned -14.7% while those passing returned
   +7.1%. On the 9,011-match backtest the gap narrowed a lot (excluded: +0.5pp
   edge / -4.8% ROI, n=80) and was flagged for re-checking. RE-VALIDATED on
   the 11,100-match dataset (2026-08-06): within the validated competitions,
   tight-eligible matches excluded by >= 4 ran -7.56% (n=88) vs +6.36% for
   those retained, and a 0/2/4/6/8 threshold sweep peaks at 4-6. The
   component stands.

**Do not add a third component without re-running the walk-forward.** Formula
complexity was tested directly and more is worse — out-of-sample ROI by
component count: 1 → +4.4%, 2 → +6.8%, 3 → +2.7%, 7 → **-0.6%**. The
seven-component version looked fine in-sample (+6.6%) and was worthless out
of it. The two-component rule is the only configuration whose out-of-sample
confidence interval excludes zero.

**There is deliberately no against-trend rule.** Betting the underdog was
worse than backing the favourite in every bucket of every signal tested; the
best fading ever managed was -2.3%, in a zone where backing lost -7.8%, so
it is "lose less", not "profit". If a genuine fade zone is found later it
belongs here, but inventing one now would be fabricating a rule the evidence
does not support.

Thresholds are **absolute**, not quantiles. A live decision on a single
upcoming match cannot ask "is this in the bottom decile" without the whole
historical distribution, so quantile cutoffs would be unusable in exactly
the situation this engine exists for.

Each decision carries `rule_version`, so a stored decision never silently
re-interprets itself when thresholds change (Oracle-handoff §7).
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

RULE_VERSION = "2026-08-06.3"

BACK_FAVOURITE = "BACK_FAVOURITE"
NO_BET = "NO_BET"

# --- v3 validity gates -------------------------------------------------------
# These are NOT signal components (the complexity warning above concerns
# formula components fitted to squeeze ROI — 3+ of those destroyed value).
# They are scope restrictions: each removes a segment where the two-component
# rule was measured to lose, each validated on the enlarged 11,100-match
# dataset with the same walk-forward machinery as the zones themselves.
#
# 1. Post-layoff skip. When either team's most recent finished match is more
#    than LAYOFF_MAX_DAYS before kickoff (season openers, returns from long
#    breaks), in-zone matches lost catastrophically: walk-forward, the
#    removed bets ran at -52.8% ROI (tight) / -52.0% (broad); skipping them
#    lifted pooled OOS ROI from +7.35% to +9.57% (tight) and +3.83% to
#    +5.38% (broad), with smooth behavior across 30/45/60-day cutoffs (not a
#    fitted cliff). Mechanism: the overdispersion signal trusts the market's
#    variance estimate, and right after a long layoff the market prices
#    tight ladders on information it doesn't actually have. 45 days clears
#    ordinary fixture gaps, international breaks, and typical winter breaks;
#    only genuine layoffs exceed it.
LAYOFF_MAX_DAYS = 45.0

# 2. Validated-competition gate. The zones were calibrated on 13
#    competitions. On 935 in-zone matches from competitions outside that
#    set (a true holdout: J1, Scottish Premiership, etc., added to live
#    tracking later), the edge collapsed from +7.3pp to +1.9pp and ROI was
#    negative (tight -2.75%, broad -3.06%). Absolute thresholds do not
#    transfer to arbitrary leagues. This set is FROZEN at this rule
#    version: a competition earns entry by re-running the walk-forward on
#    its own accumulated data — being tracked in the catalog does not,
#    by itself, make its matches bettable.
VALIDATED_COMPETITIONS = frozenset({
    "comp_3039",    # Premier League
    "comp_8814",    # LaLiga
    "comp_4643",    # Bundesliga
    "comp_5840",    # Serie A
    "comp_0256",    # Ligue 1
    "comp_3809",    # Eredivisie
    "comp_8385",    # Liga Portugal Betclic
    "comp_8321",    # Championship
    "comp_4795",    # Brasileirão Série A
    "comp_8531",    # Belgian Pro League
    "comp_298265",  # Liga MX, Apertura
    "comp_137103",  # Liga MX, Clausura
    "comp_9799",    # MLS
})

# Tier 1 leagues backtest *negative* (-7.8%) while tiers 2/3 are positive
# (+7.2%/+3.7%) — the edge lives in less efficiently priced markets, exactly
# as market-efficiency theory predicts. Not encoded as a filter: that would be
# a third component, and see the complexity warning in the module docstring.
KNOWN_TIER_HETEROGENEITY = True


@dataclass(frozen=True)
class Zone:
    """A named betting zone plus the historical performance that justified it.

    `edge_estimate` / `roi_estimate` are frozen measurements at
    `rule_version`, not live recomputations — persisting them alongside the
    version is what keeps later "did results match expectation" analysis
    honest, since both would otherwise drift as more data arrives.
    """

    name: str
    max_overdispersion: float
    min_prior_matches: int
    edge_estimate: float
    roi_estimate: float
    oos_roi: float
    sample_size: int


# Ordered tightest-first; classification takes the first match.
# `oos_roi` is the walk-forward figure — the one to trust. In-sample
# `roi_estimate` is optimistic because thresholds were chosen after seeing
# the data, and is kept only for comparison.
#
# v3 estimates: re-measured on the enlarged dataset (11,100 fitted matches,
# 2023-02 to 2026-08), restricted to VALIDATED_COMPETITIONS with the layoff
# skip applied — i.e. measured under exactly the conditions the rule now
# bets under. Thresholds themselves are UNCHANGED from v2; only the gates
# and the frozen performance estimates moved. With the gates, both zones'
# walk-forward CIs exclude zero (tight [+3.5%, +15.7%] P=0.999; broad
# [+0.5%, +10.3%] P=0.984) — under v2 conditions only tight did.
# The v2 numbers for comparison: tight edge .0765/roi .0713/oos .0676
# (n=735); broad edge .0534/roi .0306/oos .0299 (n=1305).
ZONES: tuple[Zone, ...] = (
    Zone("tight_known_teams", max_overdispersion=1.12773, min_prior_matches=4,
         edge_estimate=0.0828, roi_estimate=0.0790, oos_roi=0.0957, sample_size=936),
    Zone("broad_known_teams", max_overdispersion=1.13789, min_prior_matches=4,
         edge_estimate=0.0585, roi_estimate=0.0401, oos_roi=0.0538, sample_size=1621),
)


@dataclass(frozen=True)
class Decision:
    call: str
    zone: str
    side: str
    odds: float
    edge_estimate: float
    skip_reason: str = ""
    rule_version: str = RULE_VERSION


def classify(
    overdispersion: float,
    min_prior_matches: float,
    favourite_side: str,
    favourite_odds: float,
    days_since_last_match: float | None = None,
    competition_validated: bool = True,
) -> Decision:
    """Decide a single match. `skip_reason` records *why* we stood aside,
    which is what makes a no-bet log reviewable rather than just empty.

    The two v3 gates fire only where the two-component rule would otherwise
    bet, so every other skip keeps its more specific reason:

    - `days_since_last_match`: the LESS recently active team's gap between
      this kickoff and its previous finished match (same weakest-link logic
      as `min_prior_matches`). `None` means unknown — no skip, since the
      familiarity gate already covers teams with no history at all.
    - `competition_validated`: whether this competition is in
      VALIDATED_COMPETITIONS. Defaults True so ad-hoc/analytical callers
      that predate v3 keep their behavior; the live path and
      `classify_frame` pass the real value.
    """
    if not np.isfinite(overdispersion):
        return Decision(NO_BET, "none", "", float("nan"), 0.0, skip_reason="no_ladder_fit")
    if not np.isfinite(min_prior_matches):
        return Decision(NO_BET, "none", "", float("nan"), 0.0, skip_reason="unknown_team_history")

    for zone in ZONES:
        if overdispersion <= zone.max_overdispersion:
            if min_prior_matches < zone.min_prior_matches:
                # The shape signal fired but we do not know these teams well
                # enough to trust it — see the module docstring for current
                # numbers on this filter's justification.
                return Decision(NO_BET, "none", "", float("nan"), 0.0, skip_reason="insufficient_team_history")
            if not competition_validated:
                return Decision(NO_BET, "none", "", float("nan"), 0.0, skip_reason="unvalidated_competition")
            if days_since_last_match is not None and days_since_last_match > LAYOFF_MAX_DAYS:
                return Decision(NO_BET, "none", "", float("nan"), 0.0, skip_reason="post_layoff")
            return Decision(BACK_FAVOURITE, zone.name, favourite_side, favourite_odds, zone.edge_estimate)

    return Decision(NO_BET, "none", "", float("nan"), 0.0, skip_reason="dispersion_too_high")


def favourite_frame(df: pd.DataFrame, quoted_prob_column: str = "quoted_prob_over_2_5") -> pd.DataFrame:
    """Adds favourite/underdog framing from the de-vigged 2.5 price.

    In a two-way market the two sides are exact complements, so
    `spread = 2 * fav_prob - 1` — bucketing by spread is bucketing by
    favourite probability, one degree of freedom rather than two.
    """
    p_over = df[quoted_prob_column]
    out = df.copy()
    out["fav_side"] = np.where(p_over >= 0.5, "OVER", "UNDER")
    out["fav_prob"] = np.maximum(p_over, 1 - p_over)
    out["dog_prob"] = 1 - out["fav_prob"]
    out["spread"] = 2 * out["fav_prob"] - 1
    out["fav_odds"] = np.where(out["fav_side"] == "OVER",
                               df["goals_2_5_over_close"], df["goals_2_5_under_close"])
    out["dog_odds"] = np.where(out["fav_side"] == "OVER",
                               df["goals_2_5_under_close"], df["goals_2_5_over_close"])
    if "target_over_2_5" in df.columns:
        over_hit = df["target_over_2_5"].astype("boolean")
        out["fav_won"] = np.where(out["fav_side"] == "OVER", over_hit, ~over_hit)
    return out


def classify_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Vectorised `classify` over a framed DataFrame.

    The v3 gate inputs are read from optional columns so historical frames
    built before they existed still classify: `stale_gap` (days since the
    less-recently-active team's previous match) and `competition_id`
    (checked against VALIDATED_COMPETITIONS). Absent columns fall back to
    the gate-neutral defaults.
    """
    prior = df["min_prior_matches"] if "min_prior_matches" in df.columns else pd.Series(np.nan, index=df.index)
    gaps = df["stale_gap"].astype(float) if "stale_gap" in df.columns else pd.Series(np.nan, index=df.index)
    validated = (
        df["competition_id"].isin(VALIDATED_COMPETITIONS)
        if "competition_id" in df.columns else pd.Series(True, index=df.index)
    )
    decisions = [
        classify(
            row.overdispersion, prior_value, row.fav_side, row.fav_odds,
            days_since_last_match=None if np.isnan(gap) else float(gap),
            competition_validated=bool(is_validated),
        )
        for row, prior_value, gap, is_validated in zip(
            df.itertuples(index=False), prior.astype(float), gaps, validated
        )
    ]
    return pd.DataFrame({
        "call": [d.call for d in decisions],
        "decision_zone": [d.zone for d in decisions],
        "bet_side": [d.side for d in decisions],
        "bet_odds": [d.odds for d in decisions],
        "edge_estimate": [d.edge_estimate for d in decisions],
        "skip_reason": [d.skip_reason for d in decisions],
        "rule_version": [d.rule_version for d in decisions],
    }, index=df.index)
