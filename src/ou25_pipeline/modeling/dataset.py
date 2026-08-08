"""Loading and splitting `matches_features.csv` for model training.

The split is chronological, not random: sorting each competition's matches
by kickoff and holding out the most recent slice as test mirrors how the
model would actually be used (predicting future matches from past form),
and avoids leaking future-derived rolling/decay features into training via
a shuffled split.
"""

from pathlib import Path

import pandas as pd

ID_COLUMNS = [
    "match_id", "competition_name", "season_id", "season_name", "kickoff_utc",
    "home_team_id", "away_team_id", "home_goals_ft", "away_goals_ft", "total_goals_ft",
]
CATEGORICAL_COLUMNS = ["tier", "competition_id"]
TARGET_COLUMN = "target_over_2_5"

# The market's own de-vigged probability. Doubles as an ordinary feature and
# as the baseline every model is measured against — see `evaluate.py`.
MARKET_PROB_COLUMN = "implied_prob_over_2_5"


def load_features(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, parse_dates=["kickoff_utc"])


def feature_columns(df: pd.DataFrame) -> list[str]:
    """Numeric feature columns: everything except identifiers, the
    categorical columns (encoded separately), and the target."""
    excluded = set(ID_COLUMNS) | set(CATEGORICAL_COLUMNS) | {TARGET_COLUMN}
    return [c for c in df.columns if c not in excluded]


def chronological_split(df: pd.DataFrame, test_frac: float = 0.2) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-competition chronological split: the most recent `test_frac` of
    each competition's matches go to test, the rest to train. Splitting per
    competition (rather than one global date cutoff) keeps every league
    represented in both sets, instead of skewing test toward whichever
    leagues' seasons happen to end latest."""
    train_parts, test_parts = [], []
    for _, group in df.groupby("competition_id", group_keys=False):
        ordered = group.sort_values("kickoff_utc")
        cutoff = int(round(len(ordered) * (1 - test_frac)))
        train_parts.append(ordered.iloc[:cutoff])
        test_parts.append(ordered.iloc[cutoff:])

    train = pd.concat(train_parts).sort_values("kickoff_utc").reset_index(drop=True)
    test = pd.concat(test_parts).sort_values("kickoff_utc").reset_index(drop=True)
    return train, test
