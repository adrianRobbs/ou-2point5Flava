from sqlalchemy import text

from ou25_pipeline.models.schemas import Competition, Season, Team
from ou25_pipeline.orchestration import registration


def test_ensure_competition_registered_writes_a_new_competition(clean_db, mocker):
    mocker.patch.object(
        registration.endpoints, "get_competition",
        return_value=Competition(id="comp_new", name="New League", country="Testland", type="league"),
    )

    written = registration.ensure_competition_registered(clean_db, client=object(), competition_id="comp_new")
    assert written is True

    with clean_db.connect() as conn:
        row = conn.execute(text("SELECT name FROM competitions WHERE competition_id='comp_new'")).mappings().one()
    assert row["name"] == "New League"


def test_ensure_competition_registered_is_a_no_op_when_already_known(clean_db, mocker):
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Existing')"))
    get_competition = mocker.patch.object(registration.endpoints, "get_competition")

    written = registration.ensure_competition_registered(clean_db, client=object(), competition_id="comp_1")
    assert written is False
    get_competition.assert_not_called()  # no API call for something we already have


def test_ensure_competition_registered_handles_a_fetch_failure_gracefully(clean_db, mocker):
    mocker.patch.object(registration.endpoints, "get_competition", return_value=None)
    written = registration.ensure_competition_registered(clean_db, client=object(), competition_id="comp_missing")
    assert written is False


def test_ensure_season_registered_writes_a_new_season(clean_db, mocker):
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
    mocker.patch.object(
        registration.endpoints, "list_seasons",
        return_value=[
            Season(id="sn_other", name="Other", year="24", start_year=2024),
            Season(id="sn_target", name="25/26", year="25/26", start_year=2025),
        ],
    )

    written = registration.ensure_season_registered(clean_db, client=object(), competition_id="comp_1", season_id="sn_target")
    assert written is True

    with clean_db.connect() as conn:
        row = conn.execute(text("SELECT name FROM seasons WHERE season_id='sn_target'")).mappings().one()
    assert row["name"] == "25/26"


def test_ensure_season_registered_is_a_no_op_when_already_known(clean_db, mocker):
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
        conn.execute(text(
            "INSERT INTO seasons (season_id, competition_id, name, year) VALUES ('sn_1','comp_1','25/26','25')"
        ))
    list_seasons = mocker.patch.object(registration.endpoints, "list_seasons")

    written = registration.ensure_season_registered(clean_db, client=object(), competition_id="comp_1", season_id="sn_1")
    assert written is False
    list_seasons.assert_not_called()


def test_ensure_season_registered_handles_season_not_found(clean_db, mocker):
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
    mocker.patch.object(registration.endpoints, "list_seasons", return_value=[])
    written = registration.ensure_season_registered(clean_db, client=object(), competition_id="comp_1", season_id="sn_missing")
    assert written is False


def test_ensure_teams_registered_writes_only_missing_teams(clean_db, mocker):
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO teams (team_id, name) VALUES ('tm_known','Known FC')"))
    get_team = mocker.patch.object(
        registration.endpoints, "get_team",
        return_value=Team(id="tm_new", name="New FC", country="Testland"),
    )

    fake_client = object()
    registration.ensure_teams_registered(
        clean_db, client=fake_client, team_ids=["tm_known", "tm_new"],
        team_names={"tm_known": "Known FC", "tm_new": "New FC"},
    )

    get_team.assert_called_once_with(fake_client, "tm_new")  # never re-fetched the already-known team
    with clean_db.connect() as conn:
        names = {r[0] for r in conn.execute(text("SELECT name FROM teams"))}
    assert names == {"Known FC", "New FC"}


def test_ensure_teams_registered_falls_back_to_payload_name_when_detail_fetch_fails(clean_db, mocker):
    mocker.patch.object(registration.endpoints, "get_team", return_value=None)

    registration.ensure_teams_registered(
        clean_db, client=object(), team_ids=["tm_x"], team_names={"tm_x": "X FC"},
    )

    with clean_db.connect() as conn:
        row = conn.execute(text("SELECT name, country FROM teams WHERE team_id='tm_x'")).mappings().one()
    assert row["name"] == "X FC"
    assert row["country"] is None


def test_ensure_teams_registered_handles_empty_list(clean_db, mocker):
    get_team = mocker.patch.object(registration.endpoints, "get_team")
    registration.ensure_teams_registered(clean_db, client=object(), team_ids=[], team_names={})
    get_team.assert_not_called()


def test_ensure_fixture_registered_registers_everything_in_fk_order(clean_db, mocker):
    mocker.patch.object(
        registration.endpoints, "get_competition",
        return_value=Competition(id="comp_new", name="New League", country=None, type="league"),
    )
    mocker.patch.object(
        registration.endpoints, "list_seasons",
        return_value=[Season(id="sn_new", name="25/26", year="25/26", start_year=2025)],
    )
    mocker.patch.object(registration.endpoints, "get_team", return_value=None)

    registration.ensure_fixture_registered(
        clean_db, client=object(), competition_id="comp_new", season_id="sn_new",
        home_team_id="tm_h", away_team_id="tm_a", home_team_name="Home", away_team_name="Away",
    )

    with clean_db.connect() as conn:
        assert conn.execute(text("SELECT 1 FROM competitions WHERE competition_id='comp_new'")).first()
        assert conn.execute(text("SELECT 1 FROM seasons WHERE season_id='sn_new'")).first()
        assert conn.execute(text("SELECT 1 FROM teams WHERE team_id IN ('tm_h','tm_a')")).fetchall()
