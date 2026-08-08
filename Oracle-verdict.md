# ORACLE-SP — Validation Verdict

**Recommendation: RETIRE the 1X2 engine.** Keep the reconstruction code and this
negative result; do not build the §7 architecture.

Companion to `Oracle-handoff.md`. That document ended at an architecture decision
with its ROI figures resting on an explicitly-flagged assumption (§9 item 1: a flat
6% bookmaker margin, "the single biggest unverified assumption in the whole
analysis"). This document closes that item against **real recorded closing prices**
for 10,571 matches, and tests the §6 zones with the same walk-forward machinery that
validated the OU2.5 engine (`market/backtest.py`).

---

## 1. What was done

`probability_open` was rebuilt from `Columns-Discovery.md` (A.1 league weight, A.2
weighted form score, B.1 open probability) directly against our own database, then
joined to real Bet365 closing odds. Two deliberate departures from a naive reading,
both to *avoid* leakage rather than to change the model:

- **A.1 league weight** uses positions recomputed point-in-time from matches already
  played. Our stored `standings` are end-of-season (see `export/match_csv.py` column
  notes); using them would leak results from after the match being scored.
- **A.2 scope** ("all qualifying matches" was never fully specified) uses the last 6
  prior matches, matching A.3's own window, with the documented `exp(-days/30)` decay.

## 2. The reconstruction is faithful — the findings replicate

Reproduced on our data (n=11,244) against `Oracle-handoff.md` §5:

| Bucket | Share (ours / theirs) | FAV win (ours / theirs) |
|---|---|---|
| Maintained (≥0.9) | 0.170 / 0.18 | **0.609 / 0.616** |
| Lost some (0.5–0.9) | 0.166 / 0.16 | **0.642 / 0.607** |
| Lost heavy (0–0.5) | 0.358 / 0.33 | **0.456 / 0.452** |
| Flipped (<0) | 0.306 / 0.33 | **0.242 / 0.249** |

Baseline FAV 44.7% / DRAW 26.1% / DOG 29.2% vs their 43.9% / 26.3% / 29.8%.

**The retention mechanism is real and replicates.** Nothing below is a claim that the
original analysis was wrong or buggy. It is a claim that the mechanism does not pay.

## 3. Finding 1 — the "against-trend" insight is definitionally circular

`Flipped` means `close_gap < 0`, i.e. the market's favourite is the form model's
underdog. Measured, not inferred:

| Bucket | form-model DOG *is* the market favourite |
|---|---|
| Maintained | 0.0% |
| Lost some | 0.0% |
| Lost heavy | 1.8% |
| **Flipped** | **100.0%** |

So **"bet the underdog when Flipped" is exactly "bet the market favourite."** It is a
relabeling of the market's own opinion, not an independent signal. At real prices:

| Strategy | n | ROI |
|---|---|---|
| Flipped → bet DOG (the §6 rule) | 3,436 | **−5.50%** |
| Every match → bet market favourite (trivial baseline) | 11,244 | **−5.11%** |

The rule is *worse than the one-line baseline it reduces to.* The 47.2% DOG win rate
in Flipped is not skill — it is the market favourite's own hit rate, in the subset
where the market favourite happens to be priced shortest of the disagreement cases.

## 4. Finding 2 — every retention bucket loses at real prices

§5 finding 3 estimated these at −2.8% / −4.1% / −5.4% / −3.6% under the 6% assumption.
Actual, at recorded closing odds:

| Bucket | Bet | n | Strike | ROI |
|---|---|---|---|---|
| Maintained | FAV | 1,916 | 0.609 | −3.13% |
| Lost some | FAV | 1,862 | 0.642 | −1.40% |
| Lost heavy | FAV | 4,030 | 0.456 | −7.44% |
| Flipped | DOG | 3,436 | 0.474 | −5.50% |

Retention genuinely *filters* — 64.2% strike in "Lost some" against a 51.8% baseline
for market favourites. The market prices that filtering in. This is the standard
"the market already knows" result, and the handoff's own estimates were close to right.

## 5. Finding 3 — the §6 zones, at real prices

| Zone | Bet | n | Strike | **Real ROI** | Handoff claim |
|---|---|---|---|---|---|
| Maintained + fav_close>0.7 | FAV | 344 | 0.817 | +0.53% | 83.9% win |
| Maintained + favStrong + fav_close>0.6 | FAV | 461 | 0.755 | +0.47% | 76.5% win |
| Retention≥0.5 + favStrong + dogWeak | FAV | 754 | 0.700 | +0.98% | 67.0% win |
| Retention≥0.5 + favStrong | FAV | 1,732 | 0.638 | −2.61% | 63.6% win |
| fav_close>0.7 | FAV | 753 | 0.814 | +0.48% | +0.8% ROI |
| Flipped + dog_close>0.5 | DOG | 969 | 0.602 | −2.48% | 59.6% win |
| Flipped + dogStrong + favWeak | DOG | 114 | 0.526 | −2.04% | +13.6% ROI |
| Flipped hard + dogStrong | DOG | 404 | 0.550 | +0.33% | 53.5% win |
| Flipped hard (ret<−0.5) | DOG | 1,412 | 0.537 | −3.63% | 52.7% win |
| **Relaxed underdog rule** | DOG | 547 | 0.521 | **+4.08%** | +3.87% ROI |

Two things worth stating plainly:

- **The 6% margin assumption was fine.** Real prices reproduced the headline
  (+4.08% actual vs +3.87% assumed). §9 item 1 is closed, and it was not the problem.
- **`Flipped + favWeak + dogStrong` (+13.6% claimed) does not survive** — it comes in
  at −2.04% on real prices. The handoff correctly flagged it as failing Bonferroni;
  that caution was right.

## 6. Finding 4 — the one survivor does not clear the bar

The relaxed underdog rule (`Flipped + fav_gf<0.7 + dog_gf≥0.8` → straight DOG) is the
only zone with meaningful ROI. It fails validation on three independent grounds:

**Confidence interval includes zero.**

| Sample | n | Strike | ROI | 95% CI | P(ROI>0) |
|---|---|---|---|---|---|
| Full | 547 | 0.521 | +4.08% | **[−4.61%, +12.79%]** | 0.819 |
| Pre-2025 | 336 | 0.530 | +4.69% | [−6.31%, +15.65%] | 0.800 |
| **2025+ (held out)** | 211 | 0.507 | +3.10% | **[−11.04%, +17.16%]** | 0.662 |

The OU2.5 engine's bar — the one every zone in `decision.py` had to clear — is an
out-of-sample interval **excluding** zero. This does not come close.

**The entire edge sits in one small subgroup.**

| Tier | n | Strike | ROI |
|---|---|---|---|
| 1 | 169 | 0.598 | **+17.18%** |
| 2 | 159 | 0.478 | −4.49% |
| 3 | 119 | 0.513 | +2.14% |
| untiered | 100 | — | −2.14% |

All of the aggregate +4.08% is tier 1 (n=169). Tiers 2, 3 and untiered are at or below
zero. This is a post-hoc subgroup found *after* seeing the aggregate, so it fails
multiple-comparison correction on arrival — the identical failure mode the handoff
itself correctly identified for `Flipped + favWeak + dogStrong`. It also runs
*opposite* to the OU2.5 tier pattern (tier 1 worst there, best here), so there is no
coherent mechanism linking them.

**The thresholds sit on a coarse discrete grid.** `last3_gf` is an integer sum, so
`GF_Strength = last3_gf/5` only takes values 0, 0.2, 0.4, … A sensitivity sweep shows
`fav_gf<0.7` and `fav_gf<0.8` select *identical* match sets (both mean `last3_gf ≤ 3`).
Real ROI by the only distinct cutpoints: −1.05% (≤2) → **+4.08% (≤3)** → +1.93% (≤4).
The published threshold sits exactly on the peak, with lower ROI on both sides.

## 7. Why no modification rescues this

Three fixable flaws exist, and fixing them does not change the verdict:

1. **`GF_Strength` discreteness** (§6 above) — using last-6 or a per-match rate would
   give finer resolution. This refines a signal that is not significant to begin with.
2. **B.1 produces extreme probabilities** — the reconstructed `probability_open` has
   std 0.318 and routinely hits 0.00/1.00, because it is a ratio of decayed form sums
   with no calibration. That makes `retention_ratio = close_gap/open_gap` unstable
   whenever `open_gap` is near zero. A calibrated form model would be better behaved.
3. **But** `Oracle-handoff.md` §5 finding 6 already settled the decisive point: a clean
   form-only model, walk-forward validated, **lost to the closing line on log-loss in
   5 of 5 folds.** Every signal in this engine is derived from that form model or from
   the market. If the form component cannot beat the market, nothing built on top of it
   can, and the market component is by definition not an edge.

The engine's structure is sound and its analysis was careful. The input simply carries
no information the closing line does not already contain.

## 8. Comparison to the engine that did work

| | OU2.5 (shipped) | ORACLE-SP 1X2 |
|---|---|---|
| Best zone edge | **+5.39pp** | +3.91pp |
| Market vig | 5.71% | 5.86% |
| Zone n | 1,422 | 547 |
| Out-of-sample CI | **excludes zero** | includes zero |
| Edge broad-based? | yes | no — all in tier 1, n=169 |

OU2.5 works because it reads *distributional shape* off a 14-price odds ladder and
compares market-implied variance to realized variance — information genuinely not in
any single price. The 1X2 market offers three prices and two degrees of freedom after
normalization. There is no shape to extract.

## 9. What to keep

- **The point-in-time reconstruction code** (`probability_open`, running league tables,
  decayed form) is correct and reusable if a future signal needs form features.
- **This negative result.** Combined with parallel testing of 1X2 draws and BTTS in the
  same session (both also failed out-of-sample), the pattern is consistent: the edge
  lives in distributional mispricing on ladder markets, not in outcome markets.
- **§9 item 1 is closed.** Real prices matched the 6% assumption. Future work need not
  re-open it.

## 10. What to discard

The §7 architecture (repurposing `prediction` / `alternative_prediction` / `volatile`,
adding `decision_zone` / `edge_estimate`) should not be built for 1X2. The persistence
design itself was good — and is exactly what the OU2.5 `predictions` table already
implements, including the frozen `rule_version` reasoning from §7. That idea survived;
the market it was going to serve did not.
