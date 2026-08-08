from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from ou25_pipeline.features.team_form import build_feature_dataframe


def _insert_match(
    conn, match_id, comp_id, season_id, kickoff, home_id, away_id, home_goals, away_goals
) -> None:
    conn.execute(
        text(
            """
            INSERT INTO matches
                (match_id, competition_id, season_id, kickoff_utc, home_team_id, away_team_id,
                 home_goals_ft, away_goals_ft, status)
            VALUES (:mid, :cid, :sid, :kickoff, :home, :away, :hg, :ag, 'finished')
            """
        ),
        {
            "mid": match_id, "cid": comp_id, "sid": season_id, "kickoff": kickoff,
            "home": home_id, "away": away_id, "hg": home_goals, "ag": away_goals,
        },
    )


def _seed(engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_3039', 'Premier League')"))
        conn.execute(
            text(
                "INSERT INTO seasons (season_id, competition_id, name, year) VALUES "
                "('sn_2324', 'comp_3039', 'PL 23/24', '23/24'), "
                "('sn_2425', 'comp_3039', 'PL 24/25', '24/25')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO teams (team_id, name) VALUES "
                "('tm_A','Team A'), ('tm_B','Team B'), ('tm_C','Team C')"
            )
        )

        base = datetime(2024, 4, 1, tzinfo=timezone.utc)
        # Team A's history: 4 matches, spanning the 23/24 -> 24/25 season boundary,
        # against team C each time so team A's rolling form is easy to hand-verify.
        _insert_match(conn, "mt_1", "comp_3039", "sn_2324", base, "tm_A", "tm_C", 3, 0)
        _insert_match(conn, "mt_2", "comp_3039", "sn_2324", base + timedelta(days=7), "tm_C", "tm_A", 1, 1)
        _insert_match(conn, "mt_3", "comp_3039", "sn_2425", base + timedelta(days=120), "tm_A", "tm_C", 0, 2)
        # The match under test: team A home vs team B, after 3 prior A matches.
        _insert_match(conn, "mt_4", "comp_3039", "sn_2425", base + timedelta(days=127), "tm_A", "tm_B", 2, 2)

        # Team A's xg for mt_1/mt_2/mt_3 — mt_2 deliberately has no recorded
        # xg (NULL) to prove a single missing match doesn't NaN out the
        # whole decay window, only reduces its contributors.
        conn.execute(
            text(
                """
                INSERT INTO match_team_stats (match_id, team_id, side, shots_on_target, xg)
                VALUES ('mt_1', 'tm_A', 'home', 5, 2.5), ('mt_3', 'tm_A', 'home', 1, 0.4)
                """
            )
        )

        # Odds for the match under test.
        conn.execute(
            text(
                """
                INSERT INTO match_odds
                    (match_id, bookmaker, goals_2_5_over_close, goals_2_5_under_close,
                     btts_yes_odds_close, btts_no_odds_close,
                     home_odds_close, draw_odds_close, away_odds_close)
                VALUES ('mt_4', 'Bet365', 1.90, 2.00, 1.70, 2.10, 1.80, 3.60, 4.50)
                """
            )
        )

        # A prior meeting between A and B (before mt_4) for head-to-head.
        _insert_match(conn, "mt_h2h", "comp_3039", "sn_2324", base - timedelta(days=200), "tm_B", "tm_A", 1, 0)


def test_build_feature_dataframe_rolling_form_excludes_current_match(clean_db):
    _seed(clean_db)
    df = build_feature_dataframe(clean_db)
    row = df[df["match_id"] == "mt_4"].iloc[0]

    # Team A's full prior history, chronological: mt_h2h (away, 0-1 loss),
    # mt_1 (3-0 win, gf=3 ga=0), mt_2 (1-1 draw as away, gf=1 ga=1),
    # mt_3 (0-2 loss, gf=0 ga=2) — 4 matches total. last3_gf/ga must be the
    # 3 most recent of those (mt_1/mt_2/mt_3), excluding both mt_h2h (too
    # old for the window) and mt_4 itself (the current match).
    assert row["home_last3_gf"] == 3 + 1 + 0
    assert row["home_last3_ga"] == 0 + 1 + 2
    assert row["home_matches_played_prior"] == 4
    assert row["home_max_gf_last3"] == 3
    assert row["home_min_gf_last3"] == 0


def test_build_feature_dataframe_decay_weights_recent_more_than_distant(clean_db):
    _seed(clean_db)
    df = build_feature_dataframe(clean_db)
    row = df[df["match_id"] == "mt_4"].iloc[0]

    # mt_3 (7 days before mt_4) contributed 0 goals; mt_1/mt_2 (127/120 days
    # before) contributed 3 and 1. The decayed sum must be far below the raw
    # last3_gf sum of 4, since the two heaviest contributors are old.
    assert row["home_decay_weighted_gf"] < row["home_last3_gf"]
    assert row["home_decay_weighted_gf"] > 0


def test_build_feature_dataframe_first_match_has_no_history(clean_db):
    _seed(clean_db)
    df = build_feature_dataframe(clean_db)
    # mt_h2h is team A's actual first-ever match in the seed data (it
    # predates mt_1 by 200 days) — team A plays away in it.
    first = df[df["match_id"] == "mt_h2h"].iloc[0]
    assert first["away_matches_played_prior"] == 0
    import pandas as pd
    assert pd.isna(first["away_last3_gf"])


def test_build_feature_dataframe_head_to_head_counts_prior_meetings_only(clean_db):
    _seed(clean_db)
    df = build_feature_dataframe(clean_db)
    row = df[df["match_id"] == "mt_4"].iloc[0]
    # Only mt_h2h (B beat A 1-0, total goals 1, under 2.5) precedes mt_4.
    assert row["h2h_matches_played"] == 1
    assert row["h2h_over_2_5_rate"] == 0.0


def test_build_feature_dataframe_market_features(clean_db):
    _seed(clean_db)
    df = build_feature_dataframe(clean_db)
    row = df[df["match_id"] == "mt_4"].iloc[0]

    over_implied = 1 / 1.90
    under_implied = 1 / 2.00
    expected_over_prob = over_implied / (over_implied + under_implied)
    assert abs(row["implied_prob_over_2_5"] - expected_over_prob) < 1e-9
    assert abs(row["implied_prob_over_2_5"] + row["implied_prob_under_2_5"] - 1.0) < 1e-9

    yes_implied = 1 / 1.70
    no_implied = 1 / 2.10
    expected_yes_prob = yes_implied / (yes_implied + no_implied)
    assert abs(row["implied_prob_btts_yes"] - expected_yes_prob) < 1e-9

    home_implied = 1 / 1.80
    draw_implied = 1 / 3.60
    away_implied = 1 / 4.50
    overround_1x2 = home_implied + draw_implied + away_implied
    assert abs(row["implied_prob_home_win"] - home_implied / overround_1x2) < 1e-9
    assert (
        abs(
            row["implied_prob_home_win"] + row["implied_prob_draw"] + row["implied_prob_away_win"] - 1.0
        )
        < 1e-9
    )

    # No _open odds column exists anywhere in this provider's real data
    # (checked: 100% null across all 26 markets/lines) — the feature was
    # removed rather than left silently NaN.
    assert "over_2_5_odds_movement" not in df.columns


def test_build_feature_dataframe_xg_and_shots_on_target_rolling(clean_db):
    _seed(clean_db)
    df = build_feature_dataframe(clean_db)
    row = df[df["match_id"] == "mt_4"].iloc[0]

    # mt_1 xg=2.5, mt_2 xg=NULL (no recorded stats), mt_3 xg=0.4.
    assert abs(row["home_last3_xg"] - (2.5 + 0.4)) < 1e-9
    assert abs(row["home_last3_shots_on_target"] - (5 + 1)) < 1e-9
    # A missing match (mt_2) must not NaN out the whole decay window.
    assert row["home_decay_weighted_xg"] > 0


def test_build_feature_dataframe_empty_when_no_matches(clean_db):
    df = build_feature_dataframe(clean_db)
    assert df.empty
