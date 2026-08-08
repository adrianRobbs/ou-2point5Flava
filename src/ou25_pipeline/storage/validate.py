from dataclasses import dataclass

from sqlalchemy import Engine, text


@dataclass
class ValidationIssue:
    check: str
    detail: str


def validate_backfill(engine: Engine, competition_id: str, season_id: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    with engine.connect() as conn:
        match_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM matches WHERE competition_id = :c AND season_id = :s"
            ),
            {"c": competition_id, "s": season_id},
        ).scalar_one()
        if match_count == 0:
            issues.append(
                ValidationIssue(
                    "row_count", f"No matches found for {competition_id}/{season_id}"
                )
            )

        missing_scores = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM matches
                WHERE competition_id = :c AND season_id = :s
                  AND status = 'finished'
                  AND (home_goals_ft IS NULL OR away_goals_ft IS NULL)
                """
            ),
            {"c": competition_id, "s": season_id},
        ).scalar_one()
        if missing_scores:
            issues.append(
                ValidationIssue(
                    "null_rate",
                    f"{missing_scores} finished matches missing a final score "
                    "(the O/U 2.5 label can't be derived for these rows)",
                )
            )

        missing_kickoff = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM matches
                WHERE competition_id = :c AND season_id = :s AND kickoff_utc IS NULL
                """
            ),
            {"c": competition_id, "s": season_id},
        ).scalar_one()
        if missing_kickoff:
            issues.append(
                ValidationIssue("null_rate", f"{missing_kickoff} matches missing kickoff_utc")
            )

        # Note: match_odds/match_team_stats/etc. all carry a FK to matches.match_id,
        # so orphan child rows are impossible by construction — not worth checking.
        # What Postgres *can't* catch is the provider's own flags going unfulfilled:
        # a match marked odds_available/xg_available but with no corresponding row,
        # which points at a silent parsing or fetch bug rather than missing upstream data.
        missing_odds = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM matches m
                WHERE m.competition_id = :c AND m.season_id = :s AND m.odds_available
                  AND NOT EXISTS (SELECT 1 FROM match_odds o WHERE o.match_id = m.match_id)
                """
            ),
            {"c": competition_id, "s": season_id},
        ).scalar_one()
        if missing_odds:
            issues.append(
                ValidationIssue(
                    "missing_satellite_data",
                    f"{missing_odds} matches flagged odds_available with no match_odds rows",
                )
            )

        missing_xg = conn.execute(
            text(
                """
                SELECT COUNT(*) FROM matches m
                WHERE m.competition_id = :c AND m.season_id = :s AND m.xg_available
                  AND NOT EXISTS (
                      SELECT 1 FROM match_team_stats t
                      WHERE t.match_id = m.match_id AND t.xg IS NOT NULL
                  )
                """
            ),
            {"c": competition_id, "s": season_id},
        ).scalar_one()
        if missing_xg:
            issues.append(
                ValidationIssue(
                    "missing_satellite_data",
                    f"{missing_xg} matches flagged xg_available with no xg recorded",
                )
            )

    return issues
