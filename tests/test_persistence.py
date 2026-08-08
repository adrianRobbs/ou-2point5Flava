from datetime import datetime, timezone

from sqlalchemy import text

from ou25_pipeline.market.decision import RULE_VERSION, Decision
from ou25_pipeline.market.live import Recommendation, UpcomingMatch
from ou25_pipeline.market.persistence import (
    discover_fixtures,
    matches_awaiting_result,
    matches_needing_refresh,
    upsert_refreshed,
)


def _seed_competition(conn, competition_id: str = "comp_1", season_id: str = "sn_1") -> None:
    """Registers everything `predictions`' foreign keys now require —
    competition, season, and both default teams `_upcoming` uses — since
    it carries the same FK chain `matches` does (see registration.py)."""
    conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES (:c, 'Test')"), {"c": competition_id})
    conn.execute(text(
        "INSERT INTO seasons (season_id, competition_id, name, year) VALUES (:s, :c, '2026', '26')"
    ), {"s": season_id, "c": competition_id})
    conn.execute(text(
        "INSERT INTO teams (team_id, name) VALUES ('tm_home','Home FC'),('tm_away','Away FC') "
        "ON CONFLICT DO NOTHING"
    ))


def _upcoming(
    match_id: str,
    kickoff: datetime = datetime(2026, 9, 1, 15, tzinfo=timezone.utc),
    competition_id: str = "comp_1",
    season_id: str = "sn_1",
) -> UpcomingMatch:
    return UpcomingMatch(
        match_id=match_id, competition_id=competition_id, season_id=season_id, kickoff_utc=kickoff,
        home_team_id="tm_home", away_team_id="tm_away", home_team="Home FC", away_team="Away FC",
    )


def test_discover_fixtures_inserts_pending_rows(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn)

    n = discover_fixtures(clean_db, [_upcoming("mt_1"), _upcoming("mt_2")])
    assert n == 2

    with clean_db.connect() as conn:
        rows = conn.execute(text("SELECT match_id, status, call FROM predictions ORDER BY match_id")).mappings().all()
    assert [r["match_id"] for r in rows] == ["mt_1", "mt_2"]
    assert all(r["status"] == "pending" for r in rows)
    assert all(r["call"] == "NO_BET" for r in rows)


def test_discover_fixtures_never_overwrites_an_updated_row(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn)
    discover_fixtures(clean_db, [_upcoming("mt_1")])

    rec = Recommendation(
        match=_upcoming("mt_1"),
        decision=Decision("BACK_FAVOURITE", "tight_known_teams", "OVER", 1.9, 0.07),
        overdispersion=1.1, min_prior_matches=10, fav_prob=0.6,
    )
    upsert_refreshed(clean_db, [rec])

    # A second discover pass for the same match must not revert it to pending.
    discover_fixtures(clean_db, [_upcoming("mt_1")])

    with clean_db.connect() as conn:
        row = conn.execute(text("SELECT status, call FROM predictions WHERE match_id = 'mt_1'")).mappings().one()
    assert row["status"] == "updated"
    assert row["call"] == "BACK_FAVOURITE"


def test_discover_fixtures_handles_empty_list(clean_db):
    assert discover_fixtures(clean_db, []) == 0


def test_upsert_refreshed_writes_full_classification(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn)
    discover_fixtures(clean_db, [_upcoming("mt_1")])

    rec = Recommendation(
        match=_upcoming("mt_1"),
        decision=Decision("BACK_FAVOURITE", "tight_known_teams", "OVER", 1.85, 0.0765),
        overdispersion=1.12, min_prior_matches=8, fav_prob=0.62,
    )
    n = upsert_refreshed(clean_db, [rec])
    assert n == 1

    with clean_db.connect() as conn:
        row = conn.execute(text(
            "SELECT status, call, decision_zone, bet_side, bet_odds, fetch_count, rule_version "
            "FROM predictions WHERE match_id = 'mt_1'"
        )).mappings().one()
    assert row["status"] == "updated"
    assert row["call"] == "BACK_FAVOURITE"
    assert row["decision_zone"] == "tight_known_teams"
    assert row["bet_side"] == "OVER"
    assert float(row["bet_odds"]) == 1.85
    assert row["fetch_count"] == 1
    assert row["rule_version"] == RULE_VERSION


def test_upsert_refreshed_increments_fetch_count_and_preserves_first_fetched_at(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn)
    discover_fixtures(clean_db, [_upcoming("mt_1")])

    with clean_db.connect() as conn:
        original_first_fetched = conn.execute(
            text("SELECT first_fetched_at FROM predictions WHERE match_id='mt_1'")
        ).scalar_one()

    rec = Recommendation(
        match=_upcoming("mt_1"),
        decision=Decision("NO_BET", "none", "", float("nan"), 0.0, skip_reason="dispersion_too_high"),
        overdispersion=1.3, min_prior_matches=10, fav_prob=0.55,
    )
    upsert_refreshed(clean_db, [rec])
    upsert_refreshed(clean_db, [rec])

    with clean_db.connect() as conn:
        row = conn.execute(text(
            "SELECT fetch_count, first_fetched_at FROM predictions WHERE match_id='mt_1'"
        )).mappings().one()
    assert row["fetch_count"] == 2
    assert row["first_fetched_at"] == original_first_fetched


def test_upsert_refreshed_converts_nan_to_null(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn)
    discover_fixtures(clean_db, [_upcoming("mt_1")])

    rec = Recommendation(
        match=_upcoming("mt_1"),
        decision=Decision("NO_BET", "none", "", float("nan"), 0.0, skip_reason="no_odds_fetched"),
        overdispersion=float("nan"), min_prior_matches=0, fav_prob=float("nan"),
    )
    upsert_refreshed(clean_db, [rec])

    with clean_db.connect() as conn:
        row = conn.execute(text(
            "SELECT overdispersion, fav_prob, bet_odds FROM predictions WHERE match_id='mt_1'"
        )).mappings().one()
    assert row["overdispersion"] is None
    assert row["fav_prob"] is None
    assert row["bet_odds"] is None


def test_upsert_refreshed_handles_empty_list(clean_db):
    assert upsert_refreshed(clean_db, []) == 0


def test_matches_needing_refresh_includes_pending_and_upcoming_updated(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn)
    past = _upcoming("mt_past", kickoff=datetime(2020, 1, 1, tzinfo=timezone.utc))
    future = _upcoming("mt_future", kickoff=datetime(2099, 1, 1, tzinfo=timezone.utc))
    discover_fixtures(clean_db, [past, future])

    # mt_past becomes 'updated' but is already finished - should drop out.
    # mt_future stays 'pending' - should remain in the refresh list.
    rec = Recommendation(
        match=past, decision=Decision("BACK_FAVOURITE", "tight_known_teams", "OVER", 1.9, 0.07),
        overdispersion=1.1, min_prior_matches=10, fav_prob=0.6,
    )
    upsert_refreshed(clean_db, [rec])

    needing_refresh = matches_needing_refresh(clean_db, ["comp_1"])
    assert {m.match_id for m in needing_refresh} == {"mt_future"}


def test_matches_needing_refresh_excludes_updated_and_already_kicked_off(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn)
    match = _upcoming("mt_1", kickoff=datetime(2020, 1, 1, tzinfo=timezone.utc))
    discover_fixtures(clean_db, [match])
    rec = Recommendation(
        match=match, decision=Decision("BACK_FAVOURITE", "tight_known_teams", "OVER", 1.9, 0.07),
        overdispersion=1.1, min_prior_matches=10, fav_prob=0.6,
    )
    upsert_refreshed(clean_db, [rec])

    assert matches_needing_refresh(clean_db, ["comp_1"]) == []


def test_matches_needing_refresh_scopes_to_requested_competitions(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn, competition_id="comp_1", season_id="sn_1")
        _seed_competition(conn, competition_id="comp_2", season_id="sn_2")
    m1 = _upcoming("mt_1", competition_id="comp_1", season_id="sn_1")
    m2 = _upcoming("mt_2", competition_id="comp_2", season_id="sn_2")
    discover_fixtures(clean_db, [m1, m2])

    assert {m.match_id for m in matches_needing_refresh(clean_db, ["comp_1"])} == {"mt_1"}


def test_matches_needing_refresh_handles_empty_competition_list(clean_db):
    assert matches_needing_refresh(clean_db, []) == []


def test_matches_awaiting_result_includes_kicked_off_match_with_no_result(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn)
    past = _upcoming("mt_past", kickoff=datetime(2020, 1, 1, tzinfo=timezone.utc))
    discover_fixtures(clean_db, [past])

    assert matches_awaiting_result(clean_db, ["comp_1"]) == ["mt_past"]


def test_matches_awaiting_result_excludes_matches_not_yet_kicked_off(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn)
    future = _upcoming("mt_future", kickoff=datetime(2099, 1, 1, tzinfo=timezone.utc))
    discover_fixtures(clean_db, [future])

    assert matches_awaiting_result(clean_db, ["comp_1"]) == []


def test_matches_awaiting_result_excludes_matches_already_finished_in_matches_table(clean_db):
    with clean_db.begin() as conn:
        _seed_competition(conn)
        conn.execute(text(
            "INSERT INTO matches (match_id, competition_id, season_id, kickoff_utc, home_team_id, "
            "away_team_id, home_goals_ft, away_goals_ft, status) VALUES "
            "('mt_past','comp_1','sn_1',:kt,'tm_home','tm_away', 2, 1, 'finished')"
        ), {"kt": datetime(2020, 1, 1, tzinfo=timezone.utc)})
    past = _upcoming("mt_past", kickoff=datetime(2020, 1, 1, tzinfo=timezone.utc))
    discover_fixtures(clean_db, [past])

    assert matches_awaiting_result(clean_db, ["comp_1"]) == []


def test_matches_awaiting_result_handles_empty_competition_list(clean_db):
    assert matches_awaiting_result(clean_db, []) == []
