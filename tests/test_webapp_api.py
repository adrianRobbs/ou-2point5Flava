import csv
import io
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from ou25_pipeline.webapp.main import app
from ou25_pipeline.webapp.routers.predictions import engine_dependency


@pytest.fixture
def client(clean_db):
    app.dependency_overrides[engine_dependency] = lambda: clean_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed(conn) -> None:
    conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
    conn.execute(text(
        "INSERT INTO seasons (season_id, competition_id, name, year) VALUES ('sn_1','comp_1','2026','26')"
    ))
    conn.execute(text(
        "INSERT INTO teams (team_id, name) VALUES ('tm_h','Home FC'),('tm_a','Away FC')"
    ))
    conn.execute(text(
        "INSERT INTO predictions (match_id, rule_version, competition_id, season_id, kickoff_utc, home_team_id, "
        "away_team_id, home_team, away_team, call, decision_zone, bet_side, bet_odds, status) VALUES "
        "('mt_1','v1','comp_1','sn_1',:kt,'tm_h','tm_a','Home FC','Away FC','BACK_FAVOURITE',"
        "'tight_known_teams','OVER',1.85,'updated')"
    ), {"kt": datetime(2026, 9, 1, 15, tzinfo=timezone.utc)})


def test_list_dates_endpoint(client, clean_db):
    with clean_db.begin() as conn:
        _seed(conn)

    response = client.get("/api/dates")
    assert response.status_code == 200
    assert "2026-09-01" in response.json()["dates"]


def test_list_predictions_endpoint(client, clean_db):
    with clean_db.begin() as conn:
        _seed(conn)

    response = client.get("/api/predictions", params={"date": "2026-09-01"})
    assert response.status_code == 200
    body = response.json()
    assert body["total_assessed"] == 1
    assert body["predictions"][0]["match_id"] == "mt_1"
    assert body["predictions"][0]["bet_side"] == "OVER"


def test_list_predictions_endpoint_empty_date_returns_empty_list(client, clean_db):
    response = client.get("/api/predictions", params={"date": "2020-01-01"})
    assert response.status_code == 200
    assert response.json() == {"date": "2020-01-01", "total_assessed": 0, "predictions": []}


def test_export_predictions_matches_json_content(client, clean_db):
    with clean_db.begin() as conn:
        _seed(conn)

    json_response = client.get("/api/predictions", params={"date": "2026-09-01"})
    csv_response = client.get("/api/predictions/export", params={"date": "2026-09-01"})

    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert "predictions_2026-09-01.csv" in csv_response.headers["content-disposition"]

    rows = list(csv.DictReader(io.StringIO(csv_response.text)))
    assert len(rows) == len(json_response.json()["predictions"])
    assert rows[0]["Home Team"] == "Home FC"
    assert rows[0]["Recommendation"] == "Back Favourite"
    assert rows[0]["Bet Side"] == "OVER"


def test_export_predictions_empty_date_still_returns_valid_csv_with_headers(client, clean_db):
    response = client.get("/api/predictions/export", params={"date": "2020-01-01"})
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert rows == []
    assert "Home Team" in response.text  # header row present even with no data rows
