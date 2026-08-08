import pytest
from fastapi.testclient import TestClient

from ou25_pipeline.config import Settings
from ou25_pipeline.orchestration.daily_sync import DiscoverResult, RefreshResult
from ou25_pipeline.webapp.routers import sync
from ou25_pipeline.webapp.main import app
from ou25_pipeline.webapp.routers.backfill import settings_dependency
from ou25_pipeline.webapp.routers.predictions import engine_dependency

ADMIN_TOKEN = "test-admin-token"


def _settings(**overrides) -> Settings:
    base = {
        "THESTATSAPI_KEY": "k", "DATABASE_URL": "d",
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


def test_discover_rejects_missing_token(client):
    response = client.post("/api/sync/discover")
    assert response.status_code == 403


def test_discover_succeeds_with_correct_token(client, mocker):
    mocker.patch.object(
        sync, "discover_fixtures_for_competitions",
        return_value=DiscoverResult(total_discovered=3, per_competition={"comp_3039": 3}),
    )
    response = client.post("/api/sync/discover", headers={"X-Admin-Token": ADMIN_TOKEN})
    assert response.status_code == 200
    assert response.json() == {"total_discovered": 3, "per_competition": {"comp_3039": 3}}


def test_discover_forwards_max_days_ahead_query_param(client, mocker):
    called = mocker.patch.object(
        sync, "discover_fixtures_for_competitions", return_value=DiscoverResult()
    )
    client.post("/api/sync/discover", params={"max_days_ahead": 1}, headers={"X-Admin-Token": ADMIN_TOKEN})
    assert called.call_args.args[3] == 1


def test_discover_defaults_max_days_ahead_to_two(client, mocker):
    called = mocker.patch.object(
        sync, "discover_fixtures_for_competitions", return_value=DiscoverResult()
    )
    client.post("/api/sync/discover", headers={"X-Admin-Token": ADMIN_TOKEN})
    assert called.call_args.args[3] == 2


def test_refresh_rejects_missing_token(client):
    response = client.post("/api/sync/refresh")
    assert response.status_code == 403


def test_refresh_succeeds_with_correct_token(client, mocker):
    mocker.patch.object(
        sync, "refresh_odds_for_competitions",
        return_value=RefreshResult(total_refreshed=5, total_backed=2, total_results_synced=1),
    )
    response = client.post("/api/sync/refresh", headers={"X-Admin-Token": ADMIN_TOKEN})
    assert response.status_code == 200
    body = response.json()
    assert body["total_refreshed"] == 5
    assert body["total_backed"] == 2
    assert body["total_results_synced"] == 1
