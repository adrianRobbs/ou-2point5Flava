# ORACLE-SP — With/Against-Trend Decision Engine — Handoff

**Purpose of this document:** everything a fresh agent needs to pick up the analysis and code cleanup without the original chat history. This session started from raw data exploration and ended at an architecture decision — this doc captures the path so decisions aren't re-litigated from scratch.

---

## 1. Project goal (as clarified mid-session — read this first)

This is **not** a match-outcome prediction system. The goal is:

> Bet **with the trend** (the Favourite) by default. Identify, from historical data, the specific conditions under which it is statistically better to bet **against the trend** (the Underdog) instead. Then classify each match, after the market has closed, into "agree with the trend" or "fade the trend" — a **post-hoc classifier conditioned on where the market already sits**, not a predictor of where it will move.

Early parts of this session drifted into "can the model predict the outcome / beat the market" framing — that was a wrong turn, corrected in-session (see §5). Everything after §5 is the corrected framing and is what should be built.

**Favourite** = team with the higher `probability_open` (the form-based model estimate, not the market). Ties (open probs equal) are excluded from all analysis (~1.4% of matches).

---

## 2. Codebase map (from uploaded fragments — likely incomplete)

**All paths below are relative to `src/api/`** — e.g. `prediction_service.ts` is actually
`src/api/prediction_service.ts`. `MatchPredictor` and `oracle-model.ts` are imported via
`../../MatchPredictor` and `../../oracle-model` from inside `src/api/`, so they resolve to
paths *outside* `src/api/` (two levels up) — worth confirming their actual location when
inspecting the repo, since import depth suggests they may live at repo root or a sibling
top-level directory, not nested under `src/api/` themselves.

```
match-processor_service.ts   — ingests raw match data, computes last3_GF/GA per team,
                                writes to `processed_matches` table (camelCase fields:
                                homeLast3GF, homeLast3GA, etc). VERIFIED CORRECT.

oracle-model.ts               — exports calculateMarketAdjustedFeatures(last3GF, last3GA,
                                probOpen, probClose), buildOracleFeatureVector,
                                scoreOracleModel, classifyOracleOutcome.
                                Computes gfStrength, gaStrength, defenceResilienceFactor,
                                gfAdjustedA/B, drfAdjustedA/B. VERIFIED CORRECT — DRF formula
                                at ~line 99 uses `last3GA` as documented (see §3 re: the bug
                                that was NOT here).

prediction_service.ts         — PredictionService class. Key methods:
                                 - generatePredictions(date, override) — calls
                                   MatchPredictor.predictUpcomingMatches(), writes
                                   prediction / alternativePrediction / confidence / volatile
                                   to `matches` table.
                                 - CSV export logic (~line 870-1152) — header array and
                                   row-value array, confirmed in correct matching order.
                                 - betTier derivation (~line 1007): 
                                     SKIP  = om.volatile === true
                                     DUAL  = om.alternativePrediction is set
                                     SINGLE = neither
                                   This existing tier system is structurally what the new
                                   decision engine needs — see §7.
                                 Imports MatchPredictor (../../MatchPredictor — NOT uploaded,
                                 not inspected this session) and calculateMarketAdjustedFeatures
                                 etc from oracle-model.ts.

prediction_routes.ts          — pure Express router, no feature logic. Confirmed irrelevant
                                to the DRF bug and to feature computation generally.

Columns-DerivedA.md           — spec for every derived column (A.1–C.15). Source of truth
                                for formulas. Full text was in original upload; key formulas
                                reproduced in §4 below.

realData.csv / realData_enriched.csv
                               — historical export, ~9,000-9,400 rows, one per match, with
                                 open/close probabilities, last3 GF/GA, derived Section C
                                 columns, system prediction + confidence, actual result.
```

**Not yet uploaded / not inspected:** `MatchPredictor` (the actual model producing `prediction`/`confidence`), `db/schema.ts` (Drizzle schema — column name mappings never directly verified, only inferred consistent from usage), any migration/backfill scripts.

---

## 3. The DRF bug — found, diagnosed, and CONFIRMED FIXED

**Symptom:** in the first `realData.csv` upload, `home_defence_resilience_factor` matched `sigmoid(last3_GF − 3)` on 100% of rows and `sigmoid(last3_GA − 3)` (the documented formula) on only 13.6% — i.e., DRF was silently computed from the wrong input.

**Root cause search (exhaustive):**
- `match-processor_service.ts` — checked, correct, no swap.
- `prediction_service.ts` call site — checked, correct argument order.
- `oracle-model.ts` — checked, the formula itself is correct (`last3GA` used, not `last3GF`).
- `prediction_routes.ts` — irrelevant, no GF/GA logic at all.

**Conclusion:** none of the four uploaded files contain the bug as currently written. Working theory: `realData.csv` was a stale export generated before `calculateMarketAdjustedFeatures` was fixed, never backfilled.

**Confirmed:** a second CSV (`realData_enriched.csv`, 9,409 rows, uploaded fresh) shows DRF matching the GA formula on **100%** of rows, both sides, including the adjusted A/B variants. The fix is live in the current pipeline. **No further action needed on this bug** — it's closed. If other historical exports/tables still hold pre-fix DRF values, those would need a one-off backfill (re-run `calculateMarketAdjustedFeatures` over stored `homeLast3GF/GA`), but this wasn't confirmed necessary — only hypothesized.

---

## 4. Column glossary (condensed from Columns-DerivedA.md — full doc has the rest)

| Column | Formula | Notes |
|---|---|---|
| `probability_open` (B.1) | form-based model estimate, from weighted recent form | NOT market-derived |
| `probability_close` (B.2) | bookmaker odds, margin removed | market's view |
| `prob_change` (C.1) | `close − open` | |
| `CLV_factor` (C.2) | `close / open` | |
| `rise_drop_pct` (C.3) | `prob_change / probability_open` | used as "rise" throughout this doc |
| `GF_Strength` (C.4) | `last3_gf / 5` | |
| `GA_Strength` (C.5) | `(2 − last3_ga) / 2` | |
| `Defence_Resilience_Factor` (C.9) | `1 / (1 + exp(−(last3_ga − 3)))` | **the bug column, now fixed** — 0.05=very solid, 1.00=very leaky |
| `Baseline_drift` (C.8) | `(base_Strength − Adjusted_Score) / base_Strength` | |
| `GF/DRF_Adjusted_A` (C.11/C.12) | gated by direction of market move (`close > open`) | |
| `GF/DRF_Adjusted_B` (C.13/C.14) | gated by `abs(Baseline_drift) > abs(rise_drop_pct)` | |
| `Adjustment Selector` (C.15) | `1` if either side's prob rose close vs open, else `0` | picks A vs B |
| Gap retention | `Lost = GapDiff/OpenDiff`, `Sustained = CloseDiff/OpenDiff` | see §5 for the **retention_ratio** actually used, which is `close_gap/open_gap`, equivalent to "Sustained" |

**Retention_ratio (the working variable, not in original doc, defined this session):**
```
fav_open, dog_open   = probability_open for favourite/underdog side
fav_close, dog_close = probability_close for favourite/underdog side
open_gap  = fav_open − dog_open
close_gap = fav_close − dog_close
retention_ratio = close_gap / open_gap
```
Bucketed: `Maintained` (≥0.9), `Lost some` (0.5–0.9), `Lost heavy` (0–0.5), `Flipped` (<0, i.e. close_gap negative — favourite closes as the *weaker* team).

**Team Strength categorisation (built this session, not in original doc):**
Tier-point system, NOT a simple `GF + (1−DRF)` composite (that degenerates when DRF is buggy — this is in fact how the DRF bug was originally caught).
```
attack points: GF_Strength >= 1.0 → 2, >= 0.6 → 1, else 0
defence points: DRF <= 0.27 → 2, <= 0.731 → 1, else 0
sum >= 3 → Strong, sum == 2 → Mid, sum <= 1 → Weak
```
Applied to both raw (GF_Strength, DRF) and adjusted (GF_Adjusted, DRF_Adjusted, selected via C.15) metrics, giving `fav_cat`/`dog_cat` and `fav_acat`/`dog_acat`.

---

## 5. Key findings, in the order they were established (so contradictions are visible)

1. **Baseline:** across the full corrected dataset (n=9,191 after excluding open-prob ties and null results), Favourite wins **43.9%**, Draw **26.3%**, Underdog wins **29.8%**. Betting the favourite blind is a losing default.

2. **Retention is the dominant mechanism** (confirmed stable across two independent halves of the data, ~6 months each):

   | Retention bucket | Share | FAV win | DRAW | DOG win |
   |---|---|---|---|---|
   | Maintained (≥0.9) | 18% | 61.6% | 22.4% | 16.0% |
   | Lost some (0.5–0.9) | 16% | 60.7% | 23.6% | 15.6% |
   | Lost heavy (0–0.5) | 33% | 45.2% | 28.0% | 26.8% |
   | Flipped (<0) | 33% | 24.9% | 27.9% | 47.2% |

   Maintained + Lost some are statistically indistinguishable — treat as one "Retention ≥0.5" bucket if simplifying.

3. **Plain retention buckets do NOT clear the vig on their own** — this is important, checked explicitly late in the session:

   | Bucket | Bet | Edge vs implied | ROI @ 6% margin |
   |---|---|---|---|
   | Maintained | FAV | +2.3pp, p=0.065 | **−2.8%** |
   | Lost some | FAV | +1.3pp, p=0.329 | **−4.1%** |
   | Lost heavy | FAV | +0.1pp, p=0.928 | **−5.4%** |
   | Flipped | DOG | +1.1pp, p=0.229 | **−3.6%** |

   Only **refined sub-zones** within these buckets clear the vig (see §6). This is why the final system must not bet every match — see §8.

4. **Team/underdog strength categories add texture but not much extra edge on their own** — Flipped × dog-Strong doesn't dramatically outperform Flipped alone; the strongest refinements instead come from combining retention with **market close level** (`fav_close`, `dog_close`), not just form-based strength tier.

5. **Two specific "exception" claims were investigated and REJECTED** — do not build these into the rule set:
   - *"Favourite loses 100% retention (close_gap=0) → Favourite always wins"* — actual: n=154, FAV 40.3% / DRAW 30.5% / DOG 29.2%, p=0.373 vs baseline. Mostly matches where both sides converge to ~0.31–0.38 (near-toss-up), not favourite-dominant.
   - *"Underdog shows 0% movement → tends to draw or win"* — actual: n=185, FAV 46.5% / DRAW 25.9% / DOG 27.6%, not-losing rate 53.5% vs baseline 56.1% (**below** baseline, opposite direction from the claim). p=0.505.

6. **The prediction/confidence system is market-contaminated, not independent skill** — this was the big mid-session correction:
   - Correlation of `prediction_confidence` with `fav_open` (form): **0.032**. With `fav_close` (market): **0.526**.
   - Feature importance for predicting confidence: `fav_rise` (pure market movement) = 0.49, dominant; all form features combined < 0.1.
   - A clean form-only model (`probability_open`, last3 GF/GA, weighted W/D/L, positions, league weight — no close, no rise, no CLV) was walk-forward validated (5 expanding folds) against the market's own closing-line implied probabilities: **lost on log-loss in 5 of 5 folds.**
   - Betting the form-only model's own pick at closing prices: negative ROI in every confidence tier, no monotonic pattern.
   - **Conclusion: ORACLE-SP's `prediction` and `prediction_confidence` fields, as currently computed, carry no independent skill beyond the closing line. They should not remain the primary signal surfaced to users.** (See §7 for what replaces them.)

7. **Draws are not predictable from available features:**
   - Market's own draw-probability AUC is only 0.57 (barely above chance) — draws are close to irreducible even for bookmakers.
   - Form-only draw model lost to market baseline on log-loss in 3 of 5 walk-forward folds (worse than the outcome model above).
   - Brute-force scan of 8 candidate draw-predictive subgroups (tight goal difference, low/high scoring, close market, etc.) — nothing survived out-of-sample with both signal and adequate n.
   - **Practical consequence: FAV/DOG (excluding the draw) has no scenario where it's the best double-chance option. Don't build it into the rule set.**

---

## 6. Zones that DO clear the vig (the actual rule candidates)

All ROI figures assume a flat 6% bookmaker margin — **real prices need checking against these**, see §9 open items.

**With-trend (bet Favourite):**

| Zone | Share | FAV win | 1st/2nd half FAV win |
|---|---|---|---|
| Maintained + fav_close > 0.7 | 3.4% | **83.9%** | 84.8% / 83.2% |
| Maintained + favStrong + fav_close > 0.6 | 4.8% | 76.5% | 79.2% / 74.4% |
| Retention≥0.5 + favStrong + dogWeak | 6.6% | 67.0% | 71.6% / 63.6% |
| Retention≥0.5 + favStrong | 18.6% | 63.6% | 66.9% / 60.7% |

**Against-trend (bet Underdog):**

| Zone | Share | DOG win | 1st/2nd half DOG win |
|---|---|---|---|
| **Flipped + dog_close > 0.5** | 8.8% | **59.6%** | 60.5% / 58.4% |
| Flipped + dogStrong + favWeak | 1.4% | 57.5% | 55.9% / 58.8% |
| Flipped hard (ret < −0.5) + dogStrong | 5.2% | 53.5% | 53.8% / 53.2% |
| Flipped hard (ret < −0.5) | 14.3% | 52.7% | 54.4% / 50.9% |

**ROI-positive after 6% vig (straight bet, not double chance — see next point):**

| Zone | Bet | n | ROI | Notes |
|---|---|---|---|---|
| fav_close > 0.7 | FAV | 554 | +0.8% | Thin but replicated across 2 independent analyses |
| Flipped + fav_gf<0.7 + dog_gf≥0.8 | DOG | 503 | **+3.87%** | "relaxed underdog rule" — see forward_tracking_spec.md |
| Flipped + favWeak + dogStrong | DOG | 127 | +13.6% | Fails Bonferroni (p=0.0044 raw, 0.158 corrected across 36-cell scan). Smooth threshold decay under sensitivity testing (encouraging) but too small/unproven to stake. |
| HC(old,contaminated)+Lost heavy | FAV | 338 | +4.7% | Stable across halves (+6.3%/+3.6%) but rests on the now-discredited old confidence field — **do not use `prediction_confidence` to gate this; needs re-derivation from a clean signal or dropped** |

**Structural rule, confirmed general (not scenario-specific): straight bet beats double chance almost everywhere.** DOG/DRAW and FAV/DRAW variants were tested against every straight-bet zone above; the straight bet had higher ROI in nearly every case (e.g. relaxed underdog rule: DOG/DRAW +1.19% vs straight DOG +3.87%). Reason: double chance is the market-mirror complement of the excluded outcome, so it cannot create edge — it only trades edge for a shorter price. **Default to straight bets; only use double chance where the accuracy objective (not ROI) is what matters.**

---

## 7. Architecture decision reached this session

**Endpoint:** reuse the existing `{{baseUrl}}/predictions/generate` endpoint. Do NOT build a new endpoint. The existing schema already has the right shape:
```ts
const betTier = om.volatile ? "SKIP" : om.alternativePrediction ? "DUAL" : "SINGLE";
```
This SKIP/DUAL/SINGLE structure is what the with/against-trend classifier needs. `volatile=true` should mean "no edge zone matched, don't bet" — this already appears to be its intended meaning, just currently driven by the old confidence system.

**What changes:**
- `prediction` field: stop populating from `MatchPredictor`'s raw HOME/AWAY/DRAW output. Populate from the decision engine's pick (translate `fav_side` + call → HOME_WIN/AWAY_WIN).
- `alternative_prediction`: populate only in DUAL tier (draw attached as insurance to a thinner-edge single call — see §6, but note DUAL should be used sparingly since straight bets outperform).
- `volatile`: true only when no zone in §6 matches (i.e., mostly "Lost heavy" and residual Maintained/Flipped matches outside the refined zones — roughly 65-70% of all matches will be SKIP, this is intentional, see §8).
- **New fields to add** (not currently in schema): `decision_zone` (string, which named zone from §6 fired) and `edge_estimate` (numeric, the historical edge for that zone at the time it fired). **Both must be persisted, not derived on read** — see the reasoning at the top of the conversation this doc summarizes: `decision_zone` should be frozen to avoid silent reclassification if the rule logic changes later (same class of bug as the DRF staleness issue in §3); `edge_estimate` is a point-in-time historical statistic, not a deterministic function of match data, and would corrupt post-hoc analysis of "did realized results match expectation" if recomputed live as more data accumulates. Tie both to a `rule_version` string so rule changes don't retroactively alter historical rows.

**Open question flagged, not yet resolved:** need to grep the broader (not-yet-uploaded) codebase for other consumers of `alternative_prediction` — this session's design introduces `alternative_prediction = DRAW` attached to a non-DRAW primary pick, which may be a new combination some downstream code doesn't expect.

---

## 8. Coverage philosophy — explicitly decided, don't relitigate

**We do NOT bet all matches.** Coverage is a cost, not a goal. Confirmed empirically: the union of all positive-edge zones found this session covers **~16% of matches** (1,477 of 9,191); a rule with 100% coverage (unified double-chance accuracy rule, for reference) returned **−3.9% ROI**, while "Lost heavy" alone (33% of matches, no filtering) returned −5.4% ROI. Every match added outside a validated positive-edge zone dilutes the portfolio toward the 6% vig. The system should classify most matches as SKIP/`volatile=true` by design.

Separately: there are TWO different objectives that should not be conflated in the same rule —
- **Accuracy-maximising** (for a "verdict" / hit-rate display): the unified double-chance rule (FAV/DRAW if fav_close>0.7, DOG/DRAW if Flipped, else FAV/DRAW) gives 77.6% accuracy across ALL matches, stable across halves. This is fine for a hit-rate UI but should never be used for staking (see straight-vs-double-chance finding in §6).
- **Profit-maximising** (for actual staking): the §6 zones, straight bets, ~16% coverage.

---

## 9. Open items / cleanup needed before continuing

1. **Confirm real bookmaker prices vs the 6% flat-margin assumption used throughout.** All ROI figures in this doc are estimates using de-margined closing probabilities × 1.06. Real prices may differ; this is the single biggest unverified assumption in the whole analysis.
2. **Grep the full codebase** (not just the 4 files uploaded this session) for consumers of `prediction`, `alternative_prediction`, `volatile`, `prediction_confidence` before repurposing their meaning — see §7.
3. **Locate and inspect `MatchPredictor`** (`../../MatchPredictor`, imported in `prediction_service.ts`, never uploaded) — this is where the currently-unreliable `prediction`/`confidence` actually get computed. Needed to know exactly what to rip out / stop calling.
4. **Locate `db/schema.ts`** — Drizzle schema was never directly inspected; all column-name consistency checks this session were inferred from usage across files, not verified against the schema definition itself.
5. **Decide the fate of the old `prediction_confidence` computation** — is it deleted, kept for logging/comparison only, or repurposed? (This doc recommends: keep computing internally for reference, stop surfacing as the primary signal.)
6. **Implement `decision_zone` / `edge_estimate` as new persisted, versioned columns** per §7.
7. **Forward-tracking spec exists** (`forward_tracking_spec.md`, delivered this session) for the "relaxed underdog rule" (Flipped + fav_gf<0.7 + dog_gf≥0.8, straight DOG). Checkpoints at n=150/300/573, ETA ~13 months at current volume (~42.6 qualifying matches/month). Kill criteria: negative edge at either interim. This spec predates the final §6/§7 zone set and should be read alongside it — it covers one specific zone in more procedural detail (logging schema, bootstrap CI, multiple-comparison correction) that the broader zone list in §6 doesn't repeat.
8. **The old HC(confidence≥0.85)+Lost heavy rule (+4.7% ROI)** rested on the now-discredited `prediction_confidence` field. If a genuinely independent confidence signal is rebuilt later (form-only, validated to beat the market — which the current attempt in §5 finding 6 did NOT achieve), re-test this zone. Until then, treat it as unconfirmed.

---

## 10. Deliverables already produced this session (in case some got lost)

- `realData_enriched.csv` (this session's manual GA-recompute version — superseded by the fresh corrected export)
- `realData_categorised.csv` — corrected DRF, retention buckets, strength categories, adjuster selector, fav_result, per match (9,191 rows)
- `forward_tracking_spec.md` — full procedural spec for the relaxed underdog rule (logging schema, checkpoints, kill criteria)
- This document