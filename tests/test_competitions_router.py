import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from ou25_pipeline.config import Settings
from ou25_pipeline.orchestration import catalog
from ou25_pipeline.webapp.main import app
from ou25_pipeline.webapp.routers.backfill import settings_dependency
from ou25_pipeline.webapp.routers.predictions import engine_dependency

ADMIN_TOKEN = "test-admin-token"


def _settings(**overrides) -> Settings:
    base = {"THESTATSAPI_KEY": "k", "DATABASE_URL": "d", "BACKFILL_ADMIN_TOKEN": ADMIN_TOKEN}
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


def test_list_competitions_rejects_missing_token(client):
    response = client.get("/api/competitions")
    assert response.status_code == 403


def test_list_competitions_succeeds_with_correct_token(client, clean_db):
    with clean_db.begin() as conn:
        conn.execute(text(
            "INSERT INTO competitions (competition_id, name, is_tracked, tier) VALUES "
            "('comp_1','Premier League', true, 1), ('comp_2','Some League', false, NULL)"
        ))
    response = client.get("/api/competitions", headers={"X-Admin-Token": ADMIN_TOKEN})
    assert response.status_code == 200
    ids = {c["competition_id"] for c in response.json()}
    assert ids == {"comp_1", "comp_2"}


def test_sync_catalog_calls_the_provider_and_upserts(client, mocker):
    mocker.patch.object(catalog.endpoints, "list_competitions", return_value=[])
    response = client.post("/api/competitions/sync-catalog", headers={"X-Admin-Token": ADMIN_TOKEN})
    assert response.status_code == 200
    assert response.json() == {"synced": 0}


def test_track_flips_the_flag(client, clean_db):
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
    response = client.post(
        "/api/competitions/comp_1/track", json={"tier": 2}, headers={"X-Admin-Token": ADMIN_TOKEN}
    )
    assert response.status_code == 200
    assert response.json() == {"competition_id": "comp_1", "is_tracked": True, "tier": 2}

    with clean_db.connect() as conn:
        row = conn.execute(text("SELECT is_tracked, tier FROM competitions WHERE competition_id='comp_1'")).one()
    assert row.is_tracked is True
    assert row.tier == 2


def test_track_returns_404_for_unknown_competition(client):
    response = client.post(
        "/api/competitions/comp_missing/track", json={}, headers={"X-Admin-Token": ADMIN_TOKEN}
    )
    assert response.status_code == 404
