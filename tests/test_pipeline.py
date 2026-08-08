from datetime import datetime, timezone

from sqlalchemy import text

from ou25_pipeline.api.client import QuotaExceededError
from ou25_pipeline.models.schemas import Competition, Match, Season, Standing, TeamRef
from ou25_pipeline.orchestration import pipeline


def _match(i: int) -> Match:
    return Match.model_validate(
        {
            "id": f"mt_{i}",
            "competition_id": "comp_1",
            "season_id": "sn_1",
            "utc_date": "2025-05-25T15:00:00Z",
            "home_team": {"id": "tm_1", "name": "Home"},
            "away_team": {"id": "tm_2", "name": "Away"},
            "status": "finished",
            "score": {"home": 1, "away": 0},
        }
    )


def test_run_backfill_stops_immediately_on_quota_exceeded(clean_db, mocker):
    # 3 matches queued; the first satellite call for match 0 hits the quota
    # wall. The loop must stop right there — not mark matches 1 and 2
    # "failed" for a reason that has nothing to do with them, and not issue
    # any more doomed requests.
    matches = [_match(i) for i in range(3)]

    mocker.patch.object(
        pipeline, "resolve_competition", return_value=Competition(id="comp_1", name="Test League")
    )
    mocker.patch.object(
        pipeline,
        "resolve_season",
        return_value=Season(id="sn_1", name="Test 2024", year="24", start_year=2024),
    )
    mocker.patch.object(pipeline.endpoints, "get_standings", return_value=[])
    mocker.patch.object(pipeline.endpoints, "list_matches", return_value=matches)
    mocker.patch.object(pipeline.endpoints, "get_match_detail", side_effect=QuotaExceededError("boom"))

    summary = pipeline.run_backfill(client=object(), engine=clean_db, competition="Test", season="2024")

    assert summary.quota_exceeded is True
    assert summary.matches_processed == 0
    assert summary.matches_failed == 1
    # Only the first match's satellite fetch should have been attempted.
    assert pipeline.endpoints.get_match_detail.call_count == 1


def _standing(team_id: str, name: str, position: int, matches_played: int) -> Standing:
    return Standing.model_validate(
        {"team": {"id": team_id, "name": name}, "position": position, "matches_played": matches_played}
    )


def test_run_backfill_teams_upsert_dedupes_playoff_stage_repeats(clean_db, mocker):
    # Belgian Pro League shape: a regular-season table followed by a
    # playoff-stage table that repeats teams (see
    # assign_standing_stage_labels). The teams upsert list was built
    # straight from the standings list — same duplication bug, different
    # table, and unlike standings it has no group_label to disambiguate,
    # so ANY repeated team_id in one batch crashes it (CardinalityViolation).
    standings = [
        _standing("tm_1", "Team One", 1, 30),
        _standing("tm_2", "Team Two", 2, 30),
        _standing("tm_1", "Team One", 1, 40),  # playoff stage repeats tm_1
        _standing("tm_2", "Team Two", 2, 40),
    ]

    mocker.patch.object(
        pipeline, "resolve_competition", return_value=Competition(id="comp_1", name="Test League")
    )
    mocker.patch.object(
        pipeline,
        "resolve_season",
        return_value=Season(id="sn_1", name="Test 2024", year="24", start_year=2024),
    )
    mocker.patch.object(pipeline.endpoints, "get_standings", return_value=standings)
    mocker.patch.object(pipeline.endpoints, "list_matches", return_value=[])

    pipeline.run_backfill(client=object(), engine=clean_db, competition="Test", season="2024")

    with clean_db.connect() as conn:
        team_count = conn.execute(text("SELECT COUNT(*) FROM teams")).scalar_one()
        standings_count = conn.execute(text("SELECT COUNT(*) FROM standings")).scalar_one()
    assert team_count == 2  # deduplicated, not 4
    assert standings_count == 4  # both stages kept, disambiguated by stage label
