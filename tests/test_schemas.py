from ou25_pipeline.models.schemas import (
    Competition,
    Lineup,
    Match,
    MatchOdds,
    MatchStats,
    Season,
    Standing,
    Timeline,
)


def test_competition_parses_minimal_payload():
    c = Competition.model_validate({"id": "comp_1", "name": "Premier League", "extra_field": "ignored"})
    assert c.id == "comp_1"
    assert c.name == "Premier League"


def test_season_parses_real_shape_with_no_start_end_dates():
    # Real API: no label/start_date/end_date — name, year code, start/end years.
    payload = {
        "id": "sn_3057848",
        "name": "Premier League 24/25",
        "year": "24/25",
        "start_year": 2024,
        "end_year": 2025,
        "is_current": False,
    }
    s = Season.model_validate(payload)
    assert s.start_year == 2024
    assert s.year == "24/25"


def test_match_parses_nested_team_refs_and_score():
    payload = {
        "id": "mt_1",
        "competition_id": "comp_1",
        "season_id": "sn_1",
        "matchday": 5,
        "utc_date": "2024-09-01T15:00:00Z",
        "home_team": {"id": "tm_1", "name": "Home FC"},
        "away_team": {"id": "tm_2", "name": "Away FC"},
        "status": "finished",
        "score": {"home": 2, "away": 1, "winner": "home", "went_to_extra_time": False},
        "odds_available": True,
        "xg_available": False,
    }
    m = Match.model_validate(payload)
    assert m.home_team.id == "tm_1"
    assert m.score.home == 2
    assert m.xg_available is False


def test_standing_defaults_group_label_none_for_linear_league():
    s = Standing.model_validate(
        {"team": {"id": "tm_1", "name": "Home FC"}, "position": 1, "points": 30}
    )
    assert s.group_label is None


def test_match_stats_overview_accepts_explicit_null_for_a_stat_category():
    # Real data: the provider sends `"expected_goals": null` (not an omitted
    # key) for some matches — this crashed the pipeline mid-Bundesliga-backfill
    # before overview's value type was widened to Optional[StatSplit].
    payload = {
        "match_id": "mt_1",
        "overview": {
            "total_shots": {"all": {"home": 13, "away": 20}},
            "expected_goals": None,
        },
    }
    stats = MatchStats.model_validate(payload)
    assert stats.overview["expected_goals"] is None
    assert stats.overview["total_shots"].all.home == 13


def test_match_stats_overview_is_keyed_by_stat_name():
    payload = {
        "match_id": "mt_1",
        "overview": {
            "total_shots": {"all": {"home": 13, "away": 20}, "first_half": {"home": 6, "away": 12}},
            "expected_goals": {"all": {"home": 1.28, "away": 2.99}},
        },
    }
    stats = MatchStats.model_validate(payload)
    assert stats.overview["total_shots"].all.home == 13
    assert stats.overview["expected_goals"].all.away == 2.99


def test_match_odds_nested_markets_kept_as_raw_dict():
    payload = {
        "match_id": "mt_1",
        "bookmakers": [
            {
                "bookmaker": "Bet365",
                "markets": {
                    "match_odds": {"home": {"opening": None, "last_seen": "1.800"}},
                    "total_goals": {
                        "2.5": {
                            "over": {"opening": None, "last_seen": "1.570"},
                            "under": {"opening": None, "last_seen": "2.380"},
                        }
                    },
                },
            }
        ],
    }
    odds = MatchOdds.model_validate(payload)
    assert odds.bookmakers[0].markets["total_goals"]["2.5"]["over"]["last_seen"] == "1.570"


def test_lineup_parses_starting_xi_field_name():
    payload = {
        "match_id": "mt_1",
        "confirmed": True,
        "home": {
            "formation": "4-3-3",
            "starting_xi": [{"id": "pl_1", "position": "G", "jersey_number": 1}],
            "substitutes": [],
        },
    }
    lineup = Lineup.model_validate(payload)
    assert lineup.home.starting_xi[0].id == "pl_1"


def test_timeline_handles_empty_events_with_reason():
    t = Timeline.model_validate({"events": [], "reason": "match_not_started"})
    assert t.events == []
    assert t.reason == "match_not_started"


def test_timeline_event_nests_team_and_player_refs():
    payload = {
        "sequence": 2,
        "minute": 5,
        "type": "foul",
        "team": {"id": "tm_1", "name": "Fulham"},
        "player": {"id": "pl_1", "name": "Some Player"},
    }
    from ou25_pipeline.models.schemas import TimelineEvent

    event = TimelineEvent.model_validate(payload)
    assert event.type == "foul"
    assert event.team.id == "tm_1"
    assert event.player.id == "pl_1"
