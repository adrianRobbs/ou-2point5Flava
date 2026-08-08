import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from ou25_pipeline.config import Settings
from ou25_pipeline.orchestration import coverage
from ou25_pipeline.webapp import render_client
from ou25_pipeline.webapp.main import app
from ou25_pipeline.webapp.routers.backfill import settings_dependency
from ou25_pipeline.webapp.routers.predictions import engine_dependency

ADMIN_TOKEN = "test-admin-token"


def _settings(**overrides) -> Settings:
    base = {
        "THESTATSAPI_KEY": "k", "DATABASE_URL": "d",
        "RENDER_API_KEY": "render-key", "RENDER_SERVICE_ID": "srv_1",
        "BACKFILL_ADMIN_TOKEN": ADMIN_TOKEN,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def client(clean_db):
    app.dependency_overrides[engine_dependency] = lambda: clean_db
    app.dependency_overrides[settings_dependency] = lambda: _settings()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_competitions_rejects_missing_token(client):
    response = client.get("/api/backfill/competitions")
    assert response.status_code == 403


def test_competitions_rejects_wrong_token(client):
    response = client.get("/api/backfill/competitions", headers={"X-Admin-Token": "wrong"})
    assert response.status_code == 403


def test_competitions_rejects_every_token_when_unconfigured(clean_db):
    app.dependency_overrides[engine_dependency] = lambda: clean_db
    app.dependency_overrides[settings_dependency] = lambda: _settings(BACKFILL_ADMIN_TOKEN=None)
    try:
        response = TestClient(app).get("/api/backfill/competitions", headers={"X-Admin-Token": "anything"})
        assert response.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_competitions_succeeds_with_correct_token(client, clean_db):
    with clean_db.begin() as conn:
        conn.execute(text(
            "INSERT INTO competitions (competition_id, name, is_tracked, tier) VALUES "
            "('comp_3039','Premier League', true, 1)"
        ))
    response = client.get("/api/backfill/competitions", headers={"X-Admin-Token": ADMIN_TOKEN})
    assert response.status_code == 200
    ids = {c["competition_id"] for c in response.json()}
    assert ids == {"comp_3039"}


def test_seasons_succeeds_with_correct_token(client, mocker):
    mocker.patch.object(coverage.endpoints, "list_seasons", return_value=[])
    response = client.get("/api/backfill/competitions/comp_1/seasons", headers={"X-Admin-Token": ADMIN_TOKEN})
    assert response.status_code == 200
    assert response.json() == []


def test_coverage_succeeds_with_correct_token(client, mocker):
    mocker.patch.object(coverage.endpoints, "list_matches", return_value=[])
    response = client.get(
        "/api/backfill/competitions/comp_1/seasons/sn_1/coverage", headers={"X-Admin-Token": ADMIN_TOKEN}
    )
    assert response.status_code == 200
    assert response.json()["status"] == "not_started"


def test_trigger_sync_calls_render_with_the_assembled_command(client, mocker):
    trigger = mocker.patch.object(render_client, "trigger_job", return_value={"id": "job_1", "status": "running"})
    response = client.post(
        "/api/backfill/competitions/comp_1/seasons/sn_1/sync",
        params={"year": "24/25"},
        headers={"X-Admin-Token": ADMIN_TOKEN},
    )
    assert response.status_code == 200
    assert response.json() == {"id": "job_1", "status": "running"}
    trigger.assert_called_once_with("render-key", "srv_1", "uv run ou25-pipeline backfill --competition comp_1 --season 24/25")


def test_trigger_sync_returns_501_when_render_not_configured(clean_db):
    app.dependency_overrides[engine_dependency] = lambda: clean_db
    app.dependency_overrides[settings_dependency] = lambda: _settings(RENDER_API_KEY=None, RENDER_SERVICE_ID=None)
    try:
        response = TestClient(app).post(
            "/api/backfill/competitions/comp_1/seasons/sn_1/sync",
            params={"year": "24/25"},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert response.status_code == 501
    finally:
        app.dependency_overrides.clear()


def test_job_status_succeeds_with_correct_token(client, mocker):
    mocker.patch.object(render_client, "get_job_status", return_value={"status": "succeeded"})
    response = client.get("/api/backfill/jobs/job_1", headers={"X-Admin-Token": ADMIN_TOKEN})
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"


def test_job_status_returns_501_when_render_not_configured(clean_db):
    app.dependency_overrides[engine_dependency] = lambda: clean_db
    app.dependency_overrides[settings_dependency] = lambda: _settings(RENDER_API_KEY=None)
    try:
        response = TestClient(app).get("/api/backfill/jobs/job_1", headers={"X-Admin-Token": ADMIN_TOKEN})
        assert response.status_code == 501
    finally:
        app.dependency_overrides.clear()
