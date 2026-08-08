# Columns and how they are derived

### A.1 League / Tournament Weight (team_league_weight)

Business description: A 1–10 strength score derived from a team's current position in its league table. Used to weight the value of a match result by the quality of the opponent (or, in the tournament-position use, to express a team's own standing).

#### Raw inputs:

- Tournament type (league vs cup, plus tournament phase text, e.g. "League Phase")
- League standings table for the relevant season: each team's rank and total teams (total_teams)

#### Calculation:
```T
effective_type = "league" if (phase contains "league" AND phase contains "phase")
                 else ("league" if db_type == "league" else "cup")

if effective_type != "league" OR standings unavailable:
    weight = NULL      # cup / knockout matches, or missing table data

else:
    weight = 10 - (9 * (rank - 1) / (total_teams - 1))
```

### A.2 Weighted Form Score (team_weighted_form_score)

#### Calculation:
```Text
result_points = 2 if team won
              = 1 if draw
              = -1 if team lost

decay = EXP( -(days_between(current_match_date, historical_match_date)) / 30 )

opponent_weight = A.1 weight for the opponent, or 1 if unavailable / NULL

match_score = result_points * decay * opponent_weight

weighted_form_score = SUM(match_score) over all qualifying matches
```

### A.3 Recent Goal Tallies — Last 3 / Last 6 (team_last3_gf, team_last3_ga, team_last6_gf, team_last6_ga, plus max/min variants)

Business description: Raw goals-for and goals-against totals across a team's most recent matches — the direct input to the new Section C strength formulas (GF-Strength / GA-Strength / Defence Resilience Factor).

#### Raw inputs:

- Team's most recent 6 matches (any competition, subject to the same scope-filtering rule as A.2), sorted most-recent-first
- Each match's goals for/against from the team's perspective

#### Calculation:
```T
last6_matches = most recent 6 qualifying matches (see A.2 scope rule)
last3_matches = most recent 3 of those

last3_gf = SUM(goals_for)   over last3_matches
last3_ga = SUM(goals_against) over last3_matches
last6_gf = SUM(goals_for)   over last6_matches
last6_ga = SUM(goals_against) over last6_matches

max_gs_in_last3 = MAX(goals_for)      over last3_matches
max_gc_in_last3 = MAX(goals_against)  over last3_matches
min_gs_in_last3 = MIN(goals_for)      over last3_matches
min_gc_in_last3 = MIN(goals_against)  over last3_matches
```

### B.1 probability_open:
Business description: The model's own estimate of each team's win probability, built purely from recent weighted form (A.2) — i.e., "based on how each team has actually been playing, who's more likely to win," with no market/odds input.

#### Calculation:
```Text
H1 = home_weighted_form_score.all_matches * 1.5
H2 = home_weighted_form_score.home_only   * 4.5
H_total = H1 + H2

A1 = away_weighted_form_score.all_matches * 1.5
A2 = away_weighted_form_score.away_only   * 4.5
A_total = A1 + A2

AH_total = H_total + A_total

if AH_total == 0:
    home_prob_open = 0.5
    away_prob_open = 0.5

elif AH_total < 0 OR H_total < 0 OR A_total < 0:
    # shift all values into non-negative territory before normalising
    form_min = MIN(H1, A1)
    perspective_min = MIN(H2, A2)

    nH1 = H1 - form_min + 1
    nH2 = H2 - perspective_min + 1
    nH_total = nH1 + nH2

    nA1 = A1 - form_min + 1
    nA2 = A2 - perspective_min + 1
    nA_total = nA1 + nA2

    n_total = nH_total + nA_total

    home_prob_open = ROUND(nH_total / n_total, 2)
    away_prob_open = ROUND(nA_total / n_total, 2)

else:
    home_prob_open = ROUND(H_total / AH_total, 2)
    away_prob_open = ROUND(A_total / AH_total, 2)
```

### B.2 probability_close:

Business description: The market's assessment of each team's win probability, derived from bookmaker odds with the overround (bookmaker margin) removed.

#### Calculation:
```Text
home_implied = 1 / home_odds
draw_implied = 1 / draw_odds
away_implied = 1 / away_odds

overround = home_implied + draw_implied + away_implied   # > 1.0 in practice

home_prob_close = ROUND(home_implied / overround, 2)
away_prob_close = ROUND(away_implied / overround, 2)
```

### C.1 prob_change

Business description: How much a team's implied win probability moved between the model's own estimate and the market's current assessment — a signal of whether the market agrees with, or has moved away from, the form-based model.

#### Calculation:
```Text
prob_change = probability_close - probability_open
```

### C.2 CLV_factor

Business description: Expressed as a ratio rather than a difference — how many times larger the market's probability is than the model's probability. ("CLV" evokes betting's "closing line value" convention, here applied to model-vs-market rather than open-vs-close market movement.). Also used to calculate other variables / columns

#### Calculation:
```T
CLV_factor = probability_close / probability_open
```

### C.3 rise_drop_pct ("rise / drop %")

Business description: The probability change (C.1) expressed as a percentage move relative to the model's own baseline — "how big was the market's disagreement, relative to what the model originally thought."

#### Calculation:
```T
rise_drop_pct = prob_change / probability_open
```

### C.4 GF_Strength

Business description: A normalised measure of a team's recent scoring output — how strong their attack has looked over their last 3 matches, scaled to a workable range, 0 being the lowest and 1+ the highest. However anything above 1 is very strong. Example if teamA has 1 and teamB has 1.2, we can consider them all having a close to similar strong attack.

#### Raw inputs: team_last3_gf (A.3)

#### Calculation:

```T
GF_Strength = last3_gf / 5
```

### C.5 GA_Strength

Business description: A normalised measure of defensive solidity based on recent goals conceded — 1 shows a very defensive record and any figure in the negative shows less defensive record.

#### Raw inputs: team_last3_ga (A.3)

#### Calculation:
```T
GA_Strength = (2 - last3_ga) / 2
```

### C.6 base_Strength

Business description: A single combined "current form strength" score blending recent attacking and defensive output. Stronger teams have a higher positive number compared to very weak teams with a negative value. This is used to calculate other variables

#### Raw inputs: GF_Strength (C.4), GA_Strength (C.5)

#### Calculation:
```T
base_Strength = GF_Strength + GA_Strength
```

### C.7 Adjusted_Score

Business description: base_Strength (C.6) rescaled by the market/model disagreement ratio (C.2) — the market's view is used to pull the raw form-based strength score up or down, with the direction of the adjustment depending on whether the team's base strength is currently negative or non-negative. This also used for calculation.

#### Raw inputs: base_Strength (C.6), CLV_factor (C.2)

#### Calculation:
```T
if SIGN(base_Strength) == -1:
    Adjusted_Score = base_Strength / CLV_factor
else:
    Adjusted_Score = base_Strength * CLV_factor
```


### C.8 Baseline_drift

Business description: The proportional amount by which the market adjustment (C.7) moved the score away from its raw form-based baseline (C.6) — a normalised measure of "how much drifted from the value of the rise / drop. A sudden change can indicate some adjusted values by the bookmaker. It should be the same or close to the original rise / drop".

#### Raw inputs: base_Strength (C.6), Adjusted_Score (C.7)

#### Calculation:
```T
if base_Strength == 0:
    Baseline_drift = 0
else:
    Baseline_drift = (base_Strength - Adjusted_Score) / base_Strength
```


### C.9 Defence_Resilience_Factor (DRF)

Business description: A sigmoid-smoothed score intended to capture defensive resilience, producing a bounded 0–1 output regardless of how extreme the underlying goal tally is. 0.05 now shows a really defensive team and 1.00 shows a very weak defence. The difference between each teams Defence Resilience Factor can tell if one will have an upper hand or might be evenly matched.

#### Raw inputs: team_last3_ga (A.3)

#### Calculation:
```T
DRF = 1 / (1 + EXP( -(last3_ga - 3) ))
```

### C.10 Gap retention

Business description: This shows if the gap between the Favourite and underdog opening probs was decreased, increased or maintained as it closed. Decreased could mean formidable underdog.

#### Raw inputs: probability_open (B.1), probability_close (B.2)

#### Calculation:
```T
Open Diff = (Home_probability_open - Away_probability_open)

Close Diff = (Home_probability_close - Away_probability_close)

Gap Diff = abs(Open Diff - Close Diff)

Gap Retention:
- Lost: Gap Diff / Open Diff
- Sustained: Close Diff / Open Diff
```

### ADJUSTED VALUES Below :  
These values are more like detectors. They are compare with “GF-Strength” and “Defence Resilience Factor”. If the adjusted shows a different value than the original. It exposes that maybe the bookmaker altered the values to undervalue / overvalue. However there are cases that this indicator can show a change and it’s a red flag. Red flags normally have absurd value changes but its worth investigating to find the actual cause.

### C.11 GF_Strength_Adjusted_A

Business description: GF_Strength (C.4), boosted in proportion to the market's move (C.3) — but only applied when the market has moved in the team's favour (its win probability rose from open to close).

Raw inputs: probability_close, probability_open, rise_drop_pct (C.3), GF_Strength (C.4)

Calculation:
```T
if probability_close > probability_open:
    GF_Strength_Adjusted_A = (rise_drop_pct * GF_Strength) + GF_Strength
else:
    GF_Strength_Adjusted_A = GF_Strength
```


### C.12 Defence_Resilience_Factor_Adjusted_A

Business description: The DRF (C.9), similarly adjusted downward when the market has moved in the team's favour — the opposite direction of adjustment to C.10, floored at zero so it never goes negative.

Raw inputs: probability_close, probability_open, rise_drop_pct (C.3), Defence_Resilience_Factor (C.9)

Calculation:
```T
if probability_close > probability_open:
    candidate = DRF - (rise_drop_pct * DRF)
    DRF_Adjusted_A = 0 if candidate < 0 else candidate
else:
    DRF_Adjusted_A = DRF
```


### C.13 GF_Strength_Adjusted_B

Business description: A second variant of the attacking-strength adjustment, gated not by the direction of the market move but by whether the "drift" from the baseline model (C.8) has out-paced the raw market move itself (C.3) — i.e., applied when the market-adjustment effect (Baseline-drift) is larger in magnitude than the raw probability swing.

Raw inputs: Baseline_drift (C.8), rise_drop_pct (C.3), GF_Strength (C.4)

Calculation:
```T
if ABS(Baseline_drift) > ABS(rise_drop_pct):
    GF_Strength_Adjusted_B = (ABS(rise_drop_pct) * GF_Strength) + GF_Strength
else:
    GF_Strength_Adjusted_B = GF_Strength
```


### C.14 Defence_Resilience_Factor_Adjusted_B

Business description: The magnitude-gated counterpart to C.11 — adjusts DRF (C.9) upward, using the signed rise_drop_pct, when the baseline drift (C.8) has out-paced the raw market move, floored at zero.

Raw inputs: Baseline_drift (C.8), rise_drop_pct (C.3), Defence_Resilience_Factor (C.9)

Calculation:
```T
if ABS(Baseline_drift) > ABS(rise_drop_pct):
    candidate = DRF + (rise_drop_pct * DRF)
    DRF_Adjusted_B = 0 if candidate < 0 else candidate
else:
    DRF_Adjusted_B = DRF
```

### C.15 Adjustment Selector

Business description: This determines which Adjustment to focus on, whether A / B since both can show change at the same time. If it's 1 then use Adjustment A, 0 then use Adjustment B.

#### Raw inputs: probability_open (B.1), probability_close (B.2)

#### Calculation:
```T
if (Home_probability_close > Home_probability_open) OR (Away_probability_close > Away_probability_open) : 1 
else: 
    0
```






