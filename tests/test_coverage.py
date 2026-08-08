from datetime import datetime, timezone

from sqlalchemy import text

from ou25_pipeline.models.schemas import Match, Season
from ou25_pipeline.orchestration import coverage


def _match(match_id: str, competition_id: str = "comp_1", season_id: str = "sn_1", utc_date: str = "2026-09-01T15:00:00Z") -> Match:
    return Match.model_validate({
        "id": match_id, "competition_id": competition_id, "season_id": season_id,
        "utc_date": utc_date,
        "home_team": {"id": "tm_h", "name": "Home"}, "away_team": {"id": "tm_a", "name": "Away"},
        "status": "finished",
    })


def test_list_tracked_competitions_only_includes_explicitly_tracked(clean_db):
    with clean_db.begin() as conn:
        conn.execute(text(
            "INSERT INTO competitions (competition_id, name, is_tracked, tier) VALUES "
            "('comp_3039','Premier League', true, 1)"
        ))

    result = coverage.list_tracked_competitions(clean_db)
    ids = {c["competition_id"] for c in result}
    assert ids == {"comp_3039"}
    premier_league = result[0]
    assert premier_league["is_registered"] is True
    assert premier_league["name"] == "Premier League"
    assert premier_league["tier"] == 1


def test_list_tracked_competitions_excludes_registered_but_untracked_competitions(clean_db):
    """The confirmed behavior change: a competition that only entered the DB
    via live-discovery auto-registration (the Scottish Premiership case)
    must not silently appear here just because it's registered — only
    deliberately-tracked competitions (see orchestration/catalog.py) show up."""
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_6387','Scottish Premiership')"))

    result = coverage.list_tracked_competitions(clean_db)
    assert result == []


def test_list_seasons_with_coverage_reports_zero_for_untouched_seasons(clean_db, mocker):
    mocker.patch.object(
        coverage.endpoints, "list_seasons",
        return_value=[Season(id="sn_1", name="24/25", year="24/25", start_year=2024, is_current=False)],
    )

    result = coverage.list_seasons_with_coverage(client=object(), engine=clean_db, competition_id="comp_1")
    assert result == [{"season_id": "sn_1", "name": "24/25", "year": "24/25", "is_current": False, "our_match_count": 0}]


def test_list_seasons_with_coverage_reports_our_actual_count(clean_db, mocker):
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
        conn.execute(text("INSERT INTO seasons (season_id, competition_id, name, year) VALUES ('sn_1','comp_1','24/25','24/25')"))
        conn.execute(text("INSERT INTO teams (team_id, name) VALUES ('tm_h','H'),('tm_a','A')"))
        for i in range(3):
            conn.execute(text(
                "INSERT INTO matches (match_id, competition_id, season_id, kickoff_utc, home_team_id, "
                "away_team_id, status) VALUES (:mid,'comp_1','sn_1',:kt,'tm_h','tm_a','finished')"
            ), {"mid": f"mt_{i}", "kt": datetime(2024, 1, 1 + i, tzinfo=timezone.utc)})
    mocker.patch.object(
        coverage.endpoints, "list_seasons",
        return_value=[Season(id="sn_1", name="24/25", year="24/25", start_year=2024)],
    )

    result = coverage.list_seasons_with_coverage(client=object(), engine=clean_db, competition_id="comp_1")
    assert result[0]["our_match_count"] == 3


def test_check_season_coverage_reports_not_started(clean_db, mocker):
    mocker.patch.object(coverage.endpoints, "list_matches", return_value=[_match("mt_1"), _match("mt_2")])
    result = coverage.check_season_coverage(client=object(), engine=clean_db, competition_id="comp_1", season_id="sn_1")
    assert result == {
        "competition_id": "comp_1", "season_id": "sn_1",
        "expected_total": 2, "our_count": 0, "status": "not_started",
        "earliest_kickoff": datetime(2026, 9, 1, 15, tzinfo=timezone.utc),
        "latest_kickoff": datetime(2026, 9, 1, 15, tzinfo=timezone.utc),
    }


def test_check_season_coverage_reports_the_real_kickoff_range(clean_db, mocker):
    """The provider gives no real season start/end date at all (see
    models/schemas.py::Season) - earliest/latest kickoff has to be derived
    from the match list itself, which is what confused a real user reading
    "0/70 - not started" with no sense of whether that season had even
    begun yet."""
    mocker.patch.object(coverage.endpoints, "list_matches", return_value=[
        _match("mt_1", utc_date="2026-08-07T10:25:00Z"),
        _match("mt_2", utc_date="2026-09-12T10:00:00Z"),
        _match("mt_3", utc_date="2026-08-20T15:00:00Z"),
    ])
    result = coverage.check_season_coverage(client=object(), engine=clean_db, competition_id="comp_1", season_id="sn_1")
    assert result["earliest_kickoff"] == datetime(2026, 8, 7, 10, 25, tzinfo=timezone.utc)
    assert result["latest_kickoff"] == datetime(2026, 9, 12, 10, 0, tzinfo=timezone.utc)


def test_check_season_coverage_kickoff_range_is_none_when_no_matches(clean_db, mocker):
    mocker.patch.object(coverage.endpoints, "list_matches", return_value=[])
    result = coverage.check_season_coverage(client=object(), engine=clean_db, competition_id="comp_1", season_id="sn_1")
    assert result["earliest_kickoff"] is None
    assert result["latest_kickoff"] is None


def test_check_season_coverage_reports_partial(clean_db, mocker):
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
        conn.execute(text("INSERT INTO seasons (season_id, competition_id, name, year) VALUES ('sn_1','comp_1','24/25','24/25')"))
        conn.execute(text("INSERT INTO teams (team_id, name) VALUES ('tm_h','H'),('tm_a','A')"))
        conn.execute(text(
            "INSERT INTO matches (match_id, competition_id, season_id, kickoff_utc, home_team_id, "
            "away_team_id, status) VALUES ('mt_1','comp_1','sn_1',:kt,'tm_h','tm_a','finished')"
        ), {"kt": datetime(2024, 1, 1, tzinfo=timezone.utc)})
    mocker.patch.object(coverage.endpoints, "list_matches", return_value=[_match("mt_1"), _match("mt_2"), _match("mt_3")])

    result = coverage.check_season_coverage(client=object(), engine=clean_db, competition_id="comp_1", season_id="sn_1")
    assert result["status"] == "partial"
    assert result["our_count"] == 1
    assert result["expected_total"] == 3


def test_check_season_coverage_reports_complete(clean_db, mocker):
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
        conn.execute(text("INSERT INTO seasons (season_id, competition_id, name, year) VALUES ('sn_1','comp_1','24/25','24/25')"))
        conn.execute(text("INSERT INTO teams (team_id, name) VALUES ('tm_h','H'),('tm_a','A')"))
        conn.execute(text(
            "INSERT INTO matches (match_id, competition_id, season_id, kickoff_utc, home_team_id, "
            "away_team_id, status) VALUES ('mt_1','comp_1','sn_1',:kt,'tm_h','tm_a','finished')"
        ), {"kt": datetime(2024, 1, 1, tzinfo=timezone.utc)})
    mocker.patch.object(coverage.endpoints, "list_matches", return_value=[_match("mt_1")])

    result = coverage.check_season_coverage(client=object(), engine=clean_db, competition_id="comp_1", season_id="sn_1")
    assert result["status"] == "complete"


def test_find_coverage_gaps_only_checks_seasons_already_registered(clean_db, mocker):
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
        conn.execute(text(
            "INSERT INTO seasons (season_id, competition_id, name, year) VALUES "
            "('sn_1','comp_1','24/25','24/25'), ('sn_2','comp_1','25/26','25/26')"
        ))
        conn.execute(text("INSERT INTO teams (team_id, name) VALUES ('tm_h','H'),('tm_a','A')"))
        conn.execute(text(
            "INSERT INTO matches (match_id, competition_id, season_id, kickoff_utc, home_team_id, "
            "away_team_id, status) VALUES ('mt_1','comp_1','sn_1',:kt,'tm_h','tm_a','finished')"
        ), {"kt": datetime(2024, 1, 1, tzinfo=timezone.utc)})
    list_matches = mocker.patch.object(
        coverage.endpoints, "list_matches",
        side_effect=[[_match("mt_1")], [_match("mt_2"), _match("mt_3")]],  # sn_1 complete, sn_2 not started
    )

    gaps = coverage.find_coverage_gaps(client=object(), engine=clean_db, competition_ids=["comp_1"])

    assert list_matches.call_count == 2  # both registered seasons checked, nothing beyond them
    assert [g.season_id for g in gaps] == ["sn_2"]
    assert gaps[0].status == "not_started"
    assert gaps[0].year == "25/26"


def test_find_coverage_gaps_excludes_complete_seasons(clean_db, mocker):
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
        conn.execute(text("INSERT INTO seasons (season_id, competition_id, name, year) VALUES ('sn_1','comp_1','24/25','24/25')"))
        conn.execute(text("INSERT INTO teams (team_id, name) VALUES ('tm_h','H'),('tm_a','A')"))
        conn.execute(text(
            "INSERT INTO matches (match_id, competition_id, season_id, kickoff_utc, home_team_id, "
            "away_team_id, status) VALUES ('mt_1','comp_1','sn_1',:kt,'tm_h','tm_a','finished')"
        ), {"kt": datetime(2024, 1, 1, tzinfo=timezone.utc)})
    mocker.patch.object(coverage.endpoints, "list_matches", return_value=[_match("mt_1")])

    gaps = coverage.find_coverage_gaps(client=object(), engine=clean_db, competition_ids=["comp_1"])
    assert gaps == []


def test_find_coverage_gaps_scopes_to_requested_competitions(clean_db, mocker):
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','A'),('comp_2','B')"))
        conn.execute(text(
            "INSERT INTO seasons (season_id, competition_id, name, year) VALUES "
            "('sn_1','comp_1','24/25','24/25'), ('sn_2','comp_2','24/25','24/25')"
        ))
    mocker.patch.object(coverage.endpoints, "list_matches", return_value=[_match("mt_1"), _match("mt_2")])

    gaps = coverage.find_coverage_gaps(client=object(), engine=clean_db, competition_ids=["comp_1"])
    assert {g.competition_id for g in gaps} == {"comp_1"}


def test_find_coverage_gaps_handles_empty_competition_list(clean_db, mocker):
    list_matches = mocker.patch.object(coverage.endpoints, "list_matches")
    gaps = coverage.find_coverage_gaps(client=object(), engine=clean_db, competition_ids=[])
    assert gaps == []
    list_matches.assert_not_called()


def test_find_coverage_gaps_excludes_unplayed_seasons(clean_db, mocker):
    """The real scenario found live: a season only ever touched by
    discover-fixtures (all matches still 'scheduled', none finished yet)
    must not be reported as a gap - run_backfill only ever fetches
    status='finished' matches, so there is nothing for it to do, and
    reporting a large gap here just produces a confusing "0 processed"
    result when someone acts on it."""
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
        conn.execute(text("INSERT INTO seasons (season_id, competition_id, name, year) VALUES ('sn_1','comp_1','26/27','26/27')"))
    # find_coverage_gaps must call list_matches with status='finished' -
    # simulated here by returning [] regardless of args, matching a season
    # where nothing has been played yet.
    mocker.patch.object(coverage.endpoints, "list_matches", return_value=[])

    gaps = coverage.find_coverage_gaps(client=object(), engine=clean_db, competition_ids=["comp_1"])
    assert gaps == []


def test_find_coverage_gaps_filters_by_finished_status(clean_db, mocker):
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
        conn.execute(text("INSERT INTO seasons (season_id, competition_id, name, year) VALUES ('sn_1','comp_1','24/25','24/25')"))
    list_matches = mocker.patch.object(coverage.endpoints, "list_matches", return_value=[])

    coverage.find_coverage_gaps(client=object(), engine=clean_db, competition_ids=["comp_1"])

    assert list_matches.call_args.kwargs["status"] == "finished"
