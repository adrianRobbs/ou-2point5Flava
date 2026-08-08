from datetime import datetime, timezone

from sqlalchemy import text

from ou25_pipeline.storage.validate import validate_backfill


def _seed_minimal_match(
    engine, *, with_score: bool, odds_available: bool = False, with_odds_row: bool = False
) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1', 'Test League')")
        )
        conn.execute(
            text(
                "INSERT INTO seasons (season_id, competition_id, name, year) "
                "VALUES ('sn_1', 'comp_1', 'Test League 2024', '2024')"
            )
        )
        conn.execute(text("INSERT INTO teams (team_id, name) VALUES ('tm_1', 'Home'), ('tm_2', 'Away')"))
        goals = "2, 1" if with_score else "NULL, NULL"
        conn.execute(
            text(
                f"""
                INSERT INTO matches
                    (match_id, competition_id, season_id, kickoff_utc, home_team_id, away_team_id,
                     home_goals_ft, away_goals_ft, status, odds_available)
                VALUES ('mt_1', 'comp_1', 'sn_1', :kickoff, 'tm_1', 'tm_2', {goals}, 'finished', :odds_avail)
                """
            ),
            {"kickoff": datetime(2024, 9, 1, tzinfo=timezone.utc), "odds_avail": odds_available},
        )
        if with_odds_row:
            conn.execute(
                text(
                    """
                    INSERT INTO match_odds (match_id, bookmaker, goals_2_5_over_close)
                    VALUES ('mt_1', 'pinnacle', 1.9)
                    """
                )
            )


def test_validate_passes_on_clean_data(clean_db):
    _seed_minimal_match(clean_db, with_score=True)
    issues = validate_backfill(clean_db, "comp_1", "sn_1")
    assert issues == []


def test_validate_flags_missing_final_score(clean_db):
    _seed_minimal_match(clean_db, with_score=False)
    issues = validate_backfill(clean_db, "comp_1", "sn_1")
    assert any(i.check == "null_rate" and "final score" in i.detail for i in issues)


def test_validate_flags_odds_available_with_no_odds_row(clean_db):
    _seed_minimal_match(clean_db, with_score=True, odds_available=True, with_odds_row=False)
    issues = validate_backfill(clean_db, "comp_1", "sn_1")
    assert any(i.check == "missing_satellite_data" and "odds_available" in i.detail for i in issues)


def test_validate_passes_when_odds_available_and_present(clean_db):
    _seed_minimal_match(clean_db, with_score=True, odds_available=True, with_odds_row=True)
    issues = validate_backfill(clean_db, "comp_1", "sn_1")
    assert issues == []


def test_validate_flags_zero_matches_for_unknown_season(clean_db):
    issues = validate_backfill(clean_db, "comp_nope", "sn_nope")
    assert any(i.check == "row_count" for i in issues)
