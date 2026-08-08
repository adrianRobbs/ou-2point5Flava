import pytest
from sqlalchemy import text

from ou25_pipeline.models.schemas import Competition
from ou25_pipeline.orchestration import catalog


def _seed(conn, competition_id: str = "comp_1", name: str = "Test", is_tracked: bool = False, tier: int | None = None) -> None:
    conn.execute(
        text(
            "INSERT INTO competitions (competition_id, name, country, type, is_tracked, tier) "
            "VALUES (:id, :name, 'England', 'league', :tracked, :tier)"
        ),
        {"id": competition_id, "name": name, "tracked": is_tracked, "tier": tier},
    )


def test_sync_competition_catalog_writes_every_fetched_competition(clean_db, mocker):
    mocker.patch.object(
        catalog.endpoints, "list_competitions",
        return_value=[
            Competition(id="comp_1", name="Premier League", country="England", type="league"),
            Competition(id="comp_2", name="LaLiga", country="Spain", type="league"),
        ],
    )

    written = catalog.sync_competition_catalog(client=object(), engine=clean_db)

    assert written == 2
    with clean_db.connect() as conn:
        names = {row[0] for row in conn.execute(text("SELECT name FROM competitions"))}
    assert names == {"Premier League", "LaLiga"}


def test_sync_competition_catalog_never_clobbers_existing_tracking_decision(clean_db, mocker):
    with clean_db.begin() as conn:
        _seed(conn, competition_id="comp_1", name="Premier League", is_tracked=True, tier=1)
    mocker.patch.object(
        catalog.endpoints, "list_competitions",
        return_value=[Competition(id="comp_1", name="Premier League", country="England", type="league")],
    )

    catalog.sync_competition_catalog(client=object(), engine=clean_db)

    with clean_db.connect() as conn:
        row = conn.execute(text("SELECT is_tracked, tier FROM competitions WHERE competition_id='comp_1'")).one()
    assert row.is_tracked is True
    assert row.tier == 1


def test_list_all_competitions_reports_tracked_and_untracked(clean_db):
    with clean_db.begin() as conn:
        _seed(conn, "comp_1", "Premier League", is_tracked=True, tier=1)
        _seed(conn, "comp_2", "Some Other League", is_tracked=False)

    result = catalog.list_all_competitions(clean_db)

    by_id = {c["competition_id"]: c for c in result}
    assert by_id["comp_1"]["is_tracked"] is True
    assert by_id["comp_1"]["tier"] == 1
    assert by_id["comp_2"]["is_tracked"] is False
    assert by_id["comp_2"]["tier"] is None


def test_list_tracked_competition_ids_only_returns_tracked(clean_db):
    with clean_db.begin() as conn:
        _seed(conn, "comp_1", "Tracked", is_tracked=True, tier=1)
        _seed(conn, "comp_2", "Not tracked", is_tracked=False)

    ids = catalog.list_tracked_competition_ids(clean_db)

    assert ids == ["comp_1"]


def test_set_competition_tracked_flips_the_flag(clean_db):
    with clean_db.begin() as conn:
        _seed(conn, "comp_1", "Test", is_tracked=False)

    catalog.set_competition_tracked(clean_db, "comp_1", tracked=True, tier=2)

    with clean_db.connect() as conn:
        row = conn.execute(text("SELECT is_tracked, tier FROM competitions WHERE competition_id='comp_1'")).one()
    assert row.is_tracked is True
    assert row.tier == 2


def test_set_competition_tracked_raises_for_unknown_competition(clean_db):
    with pytest.raises(ValueError, match="comp_missing"):
        catalog.set_competition_tracked(clean_db, "comp_missing", tracked=True)
