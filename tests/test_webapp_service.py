from datetime import date, datetime, timezone

from sqlalchemy import text

from ou25_pipeline.webapp import service


def _seed_competition(conn, competition_id: str = "comp_1", season_id: str = "sn_1", name: str = "Test") -> None:
    """Registers everything `predictions`' foreign keys require: the
    competition, its season, and the two default teams `_insert_prediction`
    uses."""
    conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES (:c, :n)"), {"c": competition_id, "n": name})
    conn.execute(text(
        "INSERT INTO seasons (season_id, competition_id, name, year) VALUES (:s, :c, '2026', '26')"
    ), {"s": season_id, "c": competition_id})
    conn.execute(text(
        "INSERT INTO teams (team_id, name) VALUES ('tm_h','Home FC'),('tm_a','Away FC') ON CONFLICT DO NOTHING"
    ))


def _insert_prediction(conn, **overrides) -> None:
    row = {
        "match_id": "mt_1", "rule_version": "v1", "competition_id": "comp_1", "season_id": "sn_1",
        "kickoff_utc": datetime(2026, 9, 1, 15, tzinfo=timezone.utc),
        "home_team_id": "tm_h", "away_team_id": "tm_a", "home_team": "Home FC", "away_team": "Away FC",
        "call": "NO_BET", "decision_zone": "none", "status": "pending",
    }
    row.update(overrides)
    columns = ", ".join(row.keys())
    placeholders = ", ".join(f":{k}" for k in row.keys())
    conn.execute(text(f"INSERT INTO predictions ({columns}) VALUES ({placeholders})"), row)


def test_get_dates_with_predictions_includes_dates_with_only_skipped_matches(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn)
        _insert_prediction(conn, match_id="mt_1", call="NO_BET", status="updated", skip_reason="dispersion_too_high")

    dates = service.get_dates_with_predictions(clean_db)
    assert date(2026, 9, 1) in dates


def test_get_predictions_for_date_filters_to_pending_and_back_favourite(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn)
        _insert_prediction(conn, match_id="mt_pending", status="pending", call="NO_BET")
        _insert_prediction(conn, match_id="mt_backed", status="updated", call="BACK_FAVOURITE",
                           decision_zone="tight_known_teams", bet_side="OVER", bet_odds=1.85)
        _insert_prediction(conn, match_id="mt_skipped", status="updated", call="NO_BET",
                           skip_reason="dispersion_too_high")

    result = service.get_predictions_for_date(clean_db, date(2026, 9, 1))
    assert result["total_assessed"] == 3
    shown = {p["match_id"] for p in result["predictions"]}
    assert shown == {"mt_pending", "mt_backed"}


def test_get_predictions_for_date_computes_hit_for_finished_matches(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn)
        conn.execute(text(
            "INSERT INTO matches (match_id, competition_id, season_id, kickoff_utc, home_team_id, "
            "away_team_id, home_goals_ft, away_goals_ft, status) VALUES "
            "('mt_1','comp_1','sn_1',:kt,'tm_h','tm_a', 2, 1, 'finished')"
        ), {"kt": datetime(2026, 9, 1, 15, tzinfo=timezone.utc)})
        _insert_prediction(conn, match_id="mt_1", status="updated", call="BACK_FAVOURITE",
                           decision_zone="tight_known_teams", bet_side="OVER", bet_odds=1.85)

    result = service.get_predictions_for_date(clean_db, date(2026, 9, 1))
    row = result["predictions"][0]
    assert row["actual_total_goals"] == 3
    assert row["hit"] is True  # OVER backed, 3 > 2.5


def test_get_predictions_for_date_hit_is_none_when_match_not_finished(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn)
        _insert_prediction(conn, match_id="mt_1", status="updated", call="BACK_FAVOURITE",
                           decision_zone="tight_known_teams", bet_side="OVER", bet_odds=1.85)

    result = service.get_predictions_for_date(clean_db, date(2026, 9, 1))
    assert result["predictions"][0]["hit"] is None


def test_get_predictions_for_date_hit_is_none_for_pending_matches(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn)
        _insert_prediction(conn, match_id="mt_1", status="pending", call="NO_BET")

    result = service.get_predictions_for_date(clean_db, date(2026, 9, 1))
    assert result["predictions"][0]["hit"] is None


def test_get_predictions_for_date_returns_empty_predictions_when_all_skipped(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn)
        _insert_prediction(conn, match_id="mt_1", status="updated", call="NO_BET", skip_reason="dispersion_too_high")

    result = service.get_predictions_for_date(clean_db, date(2026, 9, 1))
    assert result["total_assessed"] == 1
    assert result["predictions"] == []


def test_get_predictions_for_date_includes_competition_name(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn, name="Liga Portugal Betclic")
        _insert_prediction(conn, match_id="mt_1", status="pending", call="NO_BET")

    result = service.get_predictions_for_date(clean_db, date(2026, 9, 1))
    assert result["predictions"][0]["competition_name"] == "Liga Portugal Betclic"


def test_get_predictions_for_date_collapses_to_latest_rule_version(clean_db):
    """A rule_version bump leaves the old row in place (PK is (match_id,
    rule_version) so history is never rewritten) and writes a new one. The
    read path must show each match once, as the LATEST version sees it —
    here the old version would have shown a BACK call, but the current
    version skips the match, so it must not appear at all."""
    with clean_db.begin() as conn:
        _seed_competition(conn)
        _insert_prediction(conn, match_id="mt_1", rule_version="2026-08-05.2", status="updated",
                           call="BACK_FAVOURITE", decision_zone="tight_known_teams",
                           bet_side="OVER", bet_odds=1.85)
        _insert_prediction(conn, match_id="mt_1", rule_version="2026-08-06.3", status="updated",
                           call="NO_BET", skip_reason="post_layoff")

    result = service.get_predictions_for_date(clean_db, date(2026, 9, 1))
    assert result["total_assessed"] == 1  # one match, not one per version
    assert result["predictions"] == []    # latest version skipped it


def test_get_predictions_for_date_latest_version_back_call_shows_once(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn)
        _insert_prediction(conn, match_id="mt_1", rule_version="2026-08-05.2", status="pending", call="NO_BET")
        _insert_prediction(conn, match_id="mt_1", rule_version="2026-08-06.3", status="updated",
                           call="BACK_FAVOURITE", decision_zone="tight_known_teams",
                           bet_side="OVER", bet_odds=1.85)

    result = service.get_predictions_for_date(clean_db, date(2026, 9, 1))
    assert len(result["predictions"]) == 1
    assert result["predictions"][0]["rule_version"] == "2026-08-06.3"


def test_get_predictions_for_date_scopes_to_the_requested_date_only(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn)
        _insert_prediction(conn, match_id="mt_today", kickoff_utc=datetime(2026, 9, 1, 15, tzinfo=timezone.utc),
                           status="pending")
        _insert_prediction(conn, match_id="mt_tomorrow", kickoff_utc=datetime(2026, 9, 2, 15, tzinfo=timezone.utc),
                           status="pending")

    result = service.get_predictions_for_date(clean_db, date(2026, 9, 1))
    assert {p["match_id"] for p in result["predictions"]} == {"mt_today"}
