from datetime import datetime, timezone

from ou25_pipeline.market.decision import Decision
from ou25_pipeline.market.live import Recommendation, UpcomingMatch
from ou25_pipeline.orchestration import daily_sync


def _upcoming(match_id: str, competition_id: str = "comp_1") -> UpcomingMatch:
    return UpcomingMatch(
        match_id=match_id, competition_id=competition_id, season_id="sn_1",
        kickoff_utc=datetime(2026, 9, 1, tzinfo=timezone.utc),
        home_team_id="tm_h", away_team_id="tm_a", home_team="Home", away_team="Away",
    )


def test_discover_fixtures_for_competitions_aggregates_across_competitions(mocker):
    mocker.patch.object(daily_sync, "list_upcoming_matches", side_effect=[
        [_upcoming("mt_1", "comp_1")],
        [_upcoming("mt_2", "comp_2"), _upcoming("mt_3", "comp_2")],
    ])
    mocker.patch.object(daily_sync, "ensure_fixture_registered")
    mocker.patch.object(daily_sync, "discover_fixtures", side_effect=[1, 2])

    result = daily_sync.discover_fixtures_for_competitions(
        client=object(), engine=object(), competition_ids=["comp_1", "comp_2"]
    )

    assert result.total_discovered == 3
    assert result.per_competition == {"comp_1": 1, "comp_2": 2}


def test_discover_fixtures_for_competitions_registers_every_match_first(mocker):
    matches = [_upcoming("mt_1"), _upcoming("mt_2")]
    mocker.patch.object(daily_sync, "list_upcoming_matches", return_value=matches)
    ensure = mocker.patch.object(daily_sync, "ensure_fixture_registered")
    mocker.patch.object(daily_sync, "discover_fixtures", return_value=2)

    daily_sync.discover_fixtures_for_competitions(client=object(), engine=object(), competition_ids=["comp_1"])

    assert ensure.call_count == 2


def test_discover_fixtures_for_competitions_forwards_max_days_ahead(mocker):
    list_upcoming = mocker.patch.object(daily_sync, "list_upcoming_matches", return_value=[])
    mocker.patch.object(daily_sync, "discover_fixtures", return_value=0)

    daily_sync.discover_fixtures_for_competitions(
        client=object(), engine=object(), competition_ids=["comp_1"], max_days_ahead=2
    )

    assert list_upcoming.call_args.kwargs["max_days_ahead"] == 2


def test_discover_fixtures_for_competitions_omits_empty_competitions_from_summary(mocker):
    mocker.patch.object(daily_sync, "list_upcoming_matches", return_value=[])
    mocker.patch.object(daily_sync, "discover_fixtures", return_value=0)

    result = daily_sync.discover_fixtures_for_competitions(
        client=object(), engine=object(), competition_ids=["comp_1"]
    )

    assert result.per_competition == {}


def test_refresh_odds_for_competitions_aggregates_refresh_and_results(mocker):
    rec_back = Recommendation(
        match=_upcoming("mt_1"),
        decision=Decision("BACK_FAVOURITE", "tight_known_teams", "OVER", 1.8, 0.07),
        overdispersion=1.1, min_prior_matches=10, fav_prob=0.6,
    )
    rec_skip = Recommendation(
        match=_upcoming("mt_2"),
        decision=Decision("NO_BET", "none", "", float("nan"), 0.0, skip_reason="dispersion_too_high"),
        overdispersion=1.3, min_prior_matches=10, fav_prob=0.5,
    )
    mocker.patch.object(daily_sync, "matches_needing_refresh", return_value=[_upcoming("mt_1"), _upcoming("mt_2")])
    mocker.patch.object(daily_sync, "classify_matches", return_value=[rec_back, rec_skip])
    mocker.patch.object(daily_sync, "upsert_refreshed", return_value=2)
    mocker.patch.object(daily_sync, "matches_awaiting_result", return_value=["mt_3"])
    mocker.patch.object(daily_sync, "sync_finished_result", return_value=True)

    result = daily_sync.refresh_odds_for_competitions(client=object(), engine=object(), competition_ids=["comp_1"])

    assert result.total_refreshed == 2
    assert result.total_backed == 1
    assert result.total_results_synced == 1
    assert result.per_competition["comp_1"] == {
        "refreshed": 2, "backed": 1, "results_synced": 1, "awaiting_result": 1,
    }


def test_refresh_odds_for_competitions_handles_nothing_pending_or_awaiting(mocker):
    mocker.patch.object(daily_sync, "matches_needing_refresh", return_value=[])
    mocker.patch.object(daily_sync, "matches_awaiting_result", return_value=[])

    result = daily_sync.refresh_odds_for_competitions(client=object(), engine=object(), competition_ids=["comp_1"])

    assert result.total_refreshed == 0
    assert result.total_results_synced == 0
    assert result.per_competition == {}


def test_refresh_odds_for_competitions_syncs_results_even_when_nothing_needs_refresh(mocker):
    mocker.patch.object(daily_sync, "matches_needing_refresh", return_value=[])
    mocker.patch.object(daily_sync, "matches_awaiting_result", return_value=["mt_1"])
    mocker.patch.object(daily_sync, "sync_finished_result", return_value=True)

    result = daily_sync.refresh_odds_for_competitions(client=object(), engine=object(), competition_ids=["comp_1"])

    assert result.total_results_synced == 1
    assert result.per_competition["comp_1"]["refreshed"] == 0
