from datetime import datetime, timezone

from sqlalchemy import text

from ou25_pipeline.export.match_csv import build_match_dataframe, write_csv


def _seed(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO competitions (competition_id, name) VALUES ('comp_3039', 'Premier League')")
        )
        conn.execute(
            text(
                "INSERT INTO seasons (season_id, competition_id, name, year) "
                "VALUES ('sn_1', 'comp_3039', 'PL 24/25', '24/25')"
            )
        )
        conn.execute(text("INSERT INTO teams (team_id, name) VALUES ('tm_1','Home FC'), ('tm_2','Away FC')"))
        conn.execute(text("INSERT INTO referees (referee_id, name, career_games) VALUES ('ref_1','Some Ref', 100)"))
        conn.execute(
            text(
                """
                INSERT INTO matches
                    (match_id, competition_id, season_id, kickoff_utc, home_team_id, away_team_id,
                     referee_id, home_goals_ft, away_goals_ft, home_goals_ht, away_goals_ht,
                     status, venue_name, venue_city, odds_available, xg_available)
                VALUES
                    ('mt_1', 'comp_3039', 'sn_1', :kickoff, 'tm_1', 'tm_2', 'ref_1',
                     2, 1, 1, 0, 'finished', 'Stadium', 'City', true, true)
                """
            ),
            {"kickoff": datetime(2024, 9, 1, tzinfo=timezone.utc)},
        )
        conn.execute(
            text(
                """
                INSERT INTO match_team_stats (match_id, team_id, side, shots_total, shots_on_target, possession_pct, corners, xg)
                VALUES ('mt_1','tm_1','home',15,6,55.5,6,1.8), ('mt_1','tm_2','away',10,3,44.5,3,1.1)
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO match_odds (match_id, bookmaker, home_odds_close, draw_odds_close, away_odds_close,
                                         goals_2_5_over_close, goals_2_5_under_close)
                VALUES ('mt_1', 'Bet365', 1.8, 3.6, 4.5, 1.57, 2.38)
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO match_lineups (match_id, team_id, side, confirmed, formation)
                VALUES ('mt_1','tm_1','home',true,'4-3-3'), ('mt_1','tm_2','away',true,'4-4-2')
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO match_events (match_id, event_seq, minute, event_type, team_id)
                VALUES
                    ('mt_1', 1, 23, 'goal', 'tm_1'),
                    ('mt_1', 2, 55, 'yellow_card', 'tm_1'),
                    ('mt_1', 3, 70, 'red_card', 'tm_2'),
                    ('mt_1', 4, 80, 'yellow_card', 'tm_2'),
                    ('mt_1', 5, 85, 'yellow_red_card', 'tm_2')
                """
            )
        )
        # Regular-season standing plus a Belgium-style playoff-stage duplicate
        # for tm_1 — build_match_dataframe must use the regular-season row.
        conn.execute(
            text(
                """
                INSERT INTO standings (season_id, team_id, group_label, position, total_teams, points, goal_difference)
                VALUES
                    ('sn_1', 'tm_1', '', 1, 16, 70, 40),
                    ('sn_1', 'tm_1', 'stage2', 1, 6, 25, 15),
                    ('sn_1', 'tm_2', '', 2, 16, 65, 30)
                """
            )
        )


def test_build_match_dataframe_shape_and_values(clean_db):
    _seed(clean_db)
    df = build_match_dataframe(clean_db)

    assert len(df) == 1
    row = df.iloc[0]
    assert row["match_id"] == "mt_1"
    assert row["total_goals_ft"] == 3
    assert bool(row["target_over_2_5"]) is True
    assert row["home_shots_total"] == 15
    assert row["away_xg"] == 1.1
    assert row["goals_2_5_over_close"] == 1.57
    assert row["home_formation"] == "4-3-3"
    assert row["away_formation"] == "4-4-2"
    assert bool(row["lineups_confirmed"]) is True
    assert row["home_yellow_cards"] == 1
    assert row["home_red_cards"] == 0
    assert row["away_yellow_cards"] == 1
    assert row["away_red_cards"] == 2  # 1 direct red + 1 second-yellow send-off
    # Regular-season row (points=70), not the stage2 duplicate (points=25).
    assert row["home_final_position"] == 1
    assert row["home_final_points"] == 70
    assert row["away_final_points"] == 65
    assert row["tier"] == 1


def test_build_match_dataframe_empty_when_no_matches(clean_db):
    df = build_match_dataframe(clean_db)
    assert df.empty


def test_write_csv_creates_sidecar_notes_file(clean_db, tmp_path):
    _seed(clean_db)
    df = build_match_dataframe(clean_db)
    output = tmp_path / "out.csv"

    notes_path = write_csv(df, output)

    assert output.exists()
    assert notes_path.exists()
    assert "post-match" in notes_path.read_text().lower()
