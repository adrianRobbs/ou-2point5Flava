"""Read path for the webapp — queries `predictions`, joined against
`matches` for the actual result once a fixture has finished.

Deliberately the single place this filtering logic exists: both the JSON
endpoint and the CSV export call `get_predictions_for_date`, so "what the
user sees" and "what they can download" can never drift apart, per the
export requirement that the CSV accurately reflect the UI at download time.
"""

from datetime import date

from sqlalchemy import Engine, text

# Applied AFTER collapsing to the latest rule_version per match —
# visibility must be judged on what the current rule says, not on a
# superseded row that happened to still be 'pending'.
_VISIBLE_FILTER = "(status = 'pending' OR call = 'BACK_FAVOURITE')"


def get_dates_with_predictions(engine: Engine) -> list[date]:
    """Every UTC calendar date that has at least one prediction row, shown
    or not. Deliberately not filtered to the visible set: a date where every
    match was assessed and correctly skipped should still be navigable, not
    silently missing from the picker — that would look like a data gap
    rather than the rule correctly deciding not to bet."""
    query = text("SELECT DISTINCT (kickoff_utc AT TIME ZONE 'UTC')::date AS d FROM predictions ORDER BY d")
    with engine.connect() as conn:
        return [row.d for row in conn.execute(query)]


def _compute_hit(row: dict) -> bool | None:
    """Whether the recommendation was right. Only meaningful once the match
    has actually finished and we made a real call, not a skip."""
    if row["match_status"] != "finished" or row["call"] != "BACK_FAVOURITE":
        return None
    if row["actual_total_goals"] is None:
        return None
    actual_over = row["actual_total_goals"] > 2.5
    return actual_over if row["bet_side"] == "OVER" else not actual_over


def get_predictions_for_date(engine: Engine, target_date: date) -> dict:
    """`{"date", "total_assessed", "predictions"}` for one UTC calendar date.

    `predictions` is filtered to what a user should actually see — a match
    still awaiting its first odds refresh ('pending'), or one that cleared
    the rule ('BACK_FAVOURITE') — never a plain skip. Skipped matches stay
    in Postgres as an audit trail (per the requirement that they don't need
    UI visibility, since they're "already recorded in the database") and
    are reflected only in `total_assessed`, not the list itself.
    """
    # A match can carry one row per rule_version (PK is (match_id,
    # rule_version) precisely so a version bump never rewrites history).
    # Reads collapse to the LATEST version per match — the date-prefixed
    # version format ("2026-08-06.3") makes lexicographic DESC the correct
    # order — so a fixture spanning a rule upgrade shows once, as the
    # current rule sees it, rather than duplicated.
    with engine.connect() as conn:
        total_assessed = conn.execute(
            text("SELECT COUNT(DISTINCT match_id) FROM predictions WHERE (kickoff_utc AT TIME ZONE 'UTC')::date = :d"),
            {"d": target_date},
        ).scalar_one()

        rows = conn.execute(text(f"""
            SELECT * FROM (
                SELECT DISTINCT ON (p.match_id)
                       p.match_id, p.rule_version, p.competition_id, c.name AS competition_name,
                       p.kickoff_utc, p.home_team, p.away_team, p.overdispersion, p.min_prior_matches,
                       p.fav_prob, p.call, p.decision_zone, p.bet_side, p.bet_odds,
                       p.edge_estimate, p.skip_reason, p.status, p.odds_updated_at,
                       p.fetch_count, m.status AS match_status,
                       (m.home_goals_ft + m.away_goals_ft) AS actual_total_goals
                FROM predictions p
                LEFT JOIN matches m ON m.match_id = p.match_id
                LEFT JOIN competitions c ON c.competition_id = p.competition_id
                WHERE (p.kickoff_utc AT TIME ZONE 'UTC')::date = :d
                ORDER BY p.match_id, p.rule_version DESC
            ) latest
            WHERE {_VISIBLE_FILTER}
            ORDER BY kickoff_utc
        """), {"d": target_date}).mappings().all()

    predictions = []
    for row in rows:
        row = dict(row)
        row["hit"] = _compute_hit(row)
        predictions.append(row)

    return {"date": target_date, "total_assessed": total_assessed, "predictions": predictions}
