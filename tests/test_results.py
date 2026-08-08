from datetime import datetime, timezone

from sqlalchemy import text

from ou25_pipeline.models.schemas import MatchDetail, Referee
from ou25_pipeline.orchestration import results


def _finished_detail(match_id: str = "mt_1") -> MatchDetail:
    return MatchDetail.model_validate({
        "id": match_id,
        "competition_id": "comp_1",
        "season_id": "sn_1",
        "utc_date": "2026-09-01T15:00:00Z",
        "home_team": {"id": "tm_home", "name": "Home FC"},
        "away_team": {"id": "tm_away", "name": "Away FC"},
        "status": "finished",
        "score": {"home": 2, "away": 1},
    })


def _seed_registered(conn) -> None:
    conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
    conn.execute(text(
        "INSERT INTO seasons (season_id, competition_id, name, year) VALUES ('sn_1','comp_1','2026','26')"
    ))
    conn.execute(text(
        "INSERT INTO teams (team_id, name) VALUES ('tm_home','Home FC'),('tm_away','Away FC')"
    ))


def test_sync_finished_result_writes_the_match_row(clean_db, mocker):
    with clean_db.begin() as conn:
        _seed_registered(conn)
    mocker.patch.object(results.endpoints, "get_match_detail", return_value=_finished_detail())
    mocker.patch.object(results.endpoints, "get_match_referee", return_value=None)

    written = results.sync_finished_result(client=object(), engine=clean_db, match_id="mt_1")
    assert written is True

    with clean_db.connect() as conn:
        row = conn.execute(text(
            "SELECT status, home_goals_ft, away_goals_ft FROM matches WHERE match_id='mt_1'"
        )).mappings().one()
    assert row["status"] == "finished"
    assert row["home_goals_ft"] == 2
    assert row["away_goals_ft"] == 1


def test_sync_finished_result_registers_a_never_seen_referee_first(clean_db, mocker):
    """The bug found live: every other test here mocks the referee as
    None, which never exercises the branch that writes matches.referee_id
    - a live-discovered match reaching 'finished' with a referee we'd
    never backfilled raised ForeignKeyViolation on every refresh-odds run
    for that competition until this was fixed."""
    with clean_db.begin() as conn:
        _seed_registered(conn)
    mocker.patch.object(results.endpoints, "get_match_detail", return_value=_finished_detail())
    mocker.patch.object(
        results.endpoints, "get_match_referee",
        return_value=Referee(id="ref_1", name="A. Referee", country="England"),
    )

    written = results.sync_finished_result(client=object(), engine=clean_db, match_id="mt_1")
    assert written is True  # must not raise ForeignKeyViolation

    with clean_db.connect() as conn:
        assert conn.execute(text("SELECT 1 FROM referees WHERE referee_id='ref_1'")).first()
        assert conn.execute(text(
            "SELECT referee_id FROM matches WHERE match_id='mt_1'"
        )).scalar_one() == "ref_1"


def test_sync_finished_result_registers_an_unbackfilled_league_first(clean_db, mocker):
    # Deliberately no _seed_registered here - this is exactly the
    # live-discovered, never-backfilled scenario the fix is for.
    from ou25_pipeline.models.schemas import Competition, Season

    mocker.patch.object(results.endpoints, "get_match_detail", return_value=_finished_detail())
    mocker.patch.object(results.endpoints, "get_match_referee", return_value=None)
    mocker.patch.object(
        results.endpoints, "get_competition",
        return_value=Competition(id="comp_1", name="Test League", country=None, type="league"),
    )
    mocker.patch.object(
        results.endpoints, "list_seasons",
        return_value=[Season(id="sn_1", name="2026", year="26", start_year=2026)],
    )
    mocker.patch.object(results.endpoints, "get_team", return_value=None)

    written = results.sync_finished_result(client=object(), engine=clean_db, match_id="mt_1")
    assert written is True

    with clean_db.connect() as conn:
        assert conn.execute(text("SELECT 1 FROM matches WHERE match_id='mt_1'")).first()


def test_sync_finished_result_returns_false_when_match_not_finished(clean_db, mocker):
    detail = _finished_detail()
    detail = detail.model_copy(update={"status": "in_play"})
    mocker.patch.object(results.endpoints, "get_match_detail", return_value=detail)

    written = results.sync_finished_result(client=object(), engine=clean_db, match_id="mt_1")
    assert written is False

    with clean_db.connect() as conn:
        assert conn.execute(text("SELECT 1 FROM matches WHERE match_id='mt_1'")).first() is None


def test_sync_finished_result_returns_false_when_fetch_fails(clean_db, mocker):
    mocker.patch.object(results.endpoints, "get_match_detail", return_value=None)
    written = results.sync_finished_result(client=object(), engine=clean_db, match_id="mt_1")
    assert written is False
