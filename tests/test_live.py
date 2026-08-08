from datetime import datetime, timezone

from sqlalchemy import text

from ou25_pipeline.market import live
from ou25_pipeline.models.schemas import BookmakerMatchOdds, Match, MatchOdds


def _match(match_id: str, odds_available: bool = True, utc_date: str = "2026-09-01T15:00:00Z") -> Match:
    return Match.model_validate({
        "id": match_id,
        "competition_id": "comp_1",
        "season_id": "sn_1",
        "utc_date": utc_date,
        "home_team": {"id": "tm_home", "name": "Home FC"},
        "away_team": {"id": "tm_away", "name": "Away FC"},
        "status": "scheduled",
        "odds_available": odds_available,
    })


def _bet365_odds(mu: float = 2.7, overround: float = 1.057) -> MatchOdds:
    """A full 0.5-6.5 ladder from a single bookmaker, fair-priced around mu."""
    from scipy.stats import poisson

    markets = {"total_goals": {}}
    for line in live.LADDER_LINES:
        p_over = min(max(float(1 - poisson.cdf(int(line), mu)), 1e-4), 1 - 1e-4)
        markets["total_goals"][str(line)] = {
            "over": {"opening": None, "last_seen": str(round(1 / (p_over * overround), 3))},
            "under": {"opening": None, "last_seen": str(round(1 / ((1 - p_over) * overround), 3))},
        }
    return MatchOdds(match_id="mt_x", bookmakers=[BookmakerMatchOdds(bookmaker="Bet365", markets=markets)])


def test_list_upcoming_matches_filters_to_odds_available(mocker):
    mocker.patch.object(
        live, "list_matches",
        return_value=[_match("mt_1", odds_available=True), _match("mt_2", odds_available=False)],
    )
    upcoming = live.list_upcoming_matches(client=object(), competition_id="comp_1")
    assert [m.match_id for m in upcoming] == ["mt_1"]


def test_list_upcoming_matches_max_days_ahead_bounds_by_kickoff(mocker):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    near = (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    far = (now + timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    mocker.patch.object(
        live, "list_matches",
        return_value=[_match("mt_near", utc_date=near), _match("mt_far", utc_date=far)],
    )

    upcoming = live.list_upcoming_matches(client=object(), competition_id="comp_1", max_days_ahead=3)
    assert [m.match_id for m in upcoming] == ["mt_near"]


def test_list_upcoming_matches_max_days_ahead_none_keeps_everything(mocker):
    mocker.patch.object(live, "list_matches", return_value=[_match("mt_1"), _match("mt_2")])
    upcoming = live.list_upcoming_matches(client=object(), competition_id="comp_1", max_days_ahead=None)
    assert len(upcoming) == 2


def test_pick_bookmaker_row_prefers_bet365_over_others():
    rows = [{"bookmaker": "SomeOtherBook", "goals_2_5_over_close": 2.0},
           {"bookmaker": "Bet365", "goals_2_5_over_close": 1.9}]
    assert live._pick_bookmaker_row(rows)["bookmaker"] == "Bet365"
    assert live._pick_bookmaker_row(list(reversed(rows)))["bookmaker"] == "Bet365"


def test_pick_bookmaker_row_falls_back_to_first_when_bet365_absent():
    rows = [{"bookmaker": "SomeOtherBook", "goals_2_5_over_close": 2.0}]
    assert live._pick_bookmaker_row(rows)["bookmaker"] == "SomeOtherBook"


def test_pick_bookmaker_row_handles_no_bookmakers():
    assert live._pick_bookmaker_row([]) is None


def test_fetch_ladder_odds_reads_the_full_ladder(mocker):
    mocker.patch.object(live, "get_match_odds", return_value=_bet365_odds(mu=2.7))
    odds = live.fetch_ladder_odds(client=object(), match_id="mt_1")
    assert odds is not None
    assert set(odds.keys()) == set(live.LADDER_LINES)
    over, under = odds[2.5]
    assert over > 1.0 and under > 1.0


def test_fetch_ladder_odds_returns_none_when_no_odds(mocker):
    mocker.patch.object(live, "get_match_odds", return_value=None)
    assert live.fetch_ladder_odds(client=object(), match_id="mt_1") is None


def test_team_known_match_counts_counts_finished_matches_from_both_sides(clean_db):
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
        conn.execute(text("INSERT INTO seasons (season_id, competition_id, name, year) VALUES ('sn_1','comp_1','24','24')"))
        conn.execute(text(
            "INSERT INTO teams (team_id, name) VALUES ('tm_a','A'),('tm_b','B'),('tm_c','C')"
        ))
        for i, (home, away, status) in enumerate([
            ("tm_a", "tm_b", "finished"),
            ("tm_b", "tm_a", "finished"),
            ("tm_a", "tm_c", "scheduled"),  # not finished - must not count
        ]):
            conn.execute(text(
                "INSERT INTO matches (match_id, competition_id, season_id, kickoff_utc, "
                "home_team_id, away_team_id, status) VALUES (:mid,'comp_1','sn_1',:kt,:h,:a,:s)"
            ), {"mid": f"mt_{i}", "kt": datetime(2024, 1, 1 + i, tzinfo=timezone.utc),
               "h": home, "a": away, "s": status})

    counts = live.team_known_match_counts(clean_db, ["tm_a", "tm_b", "tm_c"])
    assert counts["tm_a"] == 2
    assert counts["tm_b"] == 2
    assert counts["tm_c"] == 0


def test_team_known_match_counts_handles_empty_team_list(clean_db):
    assert live.team_known_match_counts(clean_db, []) == {}


def test_recommend_for_competition_backs_a_tight_low_dispersion_match(clean_db, mocker):
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
        conn.execute(text("INSERT INTO seasons (season_id, competition_id, name, year) VALUES ('sn_1','comp_1','24','24')"))
        conn.execute(text("INSERT INTO teams (team_id, name) VALUES ('tm_home','H'),('tm_away','A')"))
        # 4 finished matches each - clears the min_prior_matches threshold.
        for i in range(4):
            conn.execute(text(
                "INSERT INTO matches (match_id, competition_id, season_id, kickoff_utc, "
                "home_team_id, away_team_id, status) VALUES (:mid,'comp_1','sn_1',:kt,'tm_home','tm_away','finished')"
            ), {"mid": f"mt_hist_{i}", "kt": datetime(2024, 1, 1 + i, tzinfo=timezone.utc)})

    mocker.patch.object(live, "list_matches", return_value=[_match("mt_live")])
    # mu chosen so the fitted overdispersion lands in the tight zone - a
    # Poisson-consistent (fair) ladder has overdispersion ~1.0, comfortably
    # inside ZONES[0].max_overdispersion.
    mocker.patch.object(live, "get_match_odds", return_value=_bet365_odds(mu=2.7))

    recs = live.recommend_for_competition(client=object(), engine=clean_db, competition_id="comp_1")
    assert len(recs) == 1
    rec = recs[0]
    assert rec.min_prior_matches == 4
    assert rec.decision.call == "BACK_FAVOURITE"
    assert rec.decision.side in ("OVER", "UNDER")


def test_recommend_for_competition_skips_unfamiliar_teams(clean_db, mocker):
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
        conn.execute(text("INSERT INTO seasons (season_id, competition_id, name, year) VALUES ('sn_1','comp_1','24','24')"))
        conn.execute(text("INSERT INTO teams (team_id, name) VALUES ('tm_home','H'),('tm_away','A')"))
        # No prior finished matches recorded for either team at all.

    mocker.patch.object(live, "list_matches", return_value=[_match("mt_live")])
    mocker.patch.object(live, "get_match_odds", return_value=_bet365_odds(mu=2.7))

    recs = live.recommend_for_competition(client=object(), engine=clean_db, competition_id="comp_1")
    assert recs[0].decision.call == "NO_BET"
    assert recs[0].decision.skip_reason == "insufficient_team_history"


def test_recommend_for_competition_handles_missing_odds_gracefully(clean_db, mocker):
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
        conn.execute(text("INSERT INTO seasons (season_id, competition_id, name, year) VALUES ('sn_1','comp_1','24','24')"))
        conn.execute(text("INSERT INTO teams (team_id, name) VALUES ('tm_home','H'),('tm_away','A')"))

    mocker.patch.object(live, "list_matches", return_value=[_match("mt_live")])
    mocker.patch.object(live, "get_match_odds", return_value=None)

    recs = live.recommend_for_competition(client=object(), engine=clean_db, competition_id="comp_1")
    assert recs[0].decision.call == "NO_BET"
    assert recs[0].decision.skip_reason == "no_odds_fetched"


def test_recommend_for_competition_returns_empty_when_no_upcoming_matches(clean_db, mocker):
    mocker.patch.object(live, "list_matches", return_value=[])
    assert live.recommend_for_competition(client=object(), engine=clean_db, competition_id="comp_1") == []


def test_recommendations_to_frame_round_trips_all_fields(clean_db, mocker):
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
        conn.execute(text("INSERT INTO seasons (season_id, competition_id, name, year) VALUES ('sn_1','comp_1','24','24')"))
        conn.execute(text("INSERT INTO teams (team_id, name) VALUES ('tm_home','H'),('tm_away','A')"))
        for i in range(4):
            conn.execute(text(
                "INSERT INTO matches (match_id, competition_id, season_id, kickoff_utc, "
                "home_team_id, away_team_id, status) VALUES (:mid,'comp_1','sn_1',:kt,'tm_home','tm_away','finished')"
            ), {"mid": f"mt_hist_{i}", "kt": datetime(2024, 1, 1 + i, tzinfo=timezone.utc)})

    mocker.patch.object(live, "list_matches", return_value=[_match("mt_live")])
    mocker.patch.object(live, "get_match_odds", return_value=_bet365_odds(mu=2.7))

    recs = live.recommend_for_competition(client=object(), engine=clean_db, competition_id="comp_1")
    df = live.recommendations_to_frame(recs)
    assert len(df) == 1
    assert df.loc[0, "match_id"] == "mt_live"
    assert df.loc[0, "rule_version"]
