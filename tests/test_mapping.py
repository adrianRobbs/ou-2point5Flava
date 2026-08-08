from ou25_pipeline.models.schemas import Lineup, Match, MatchDetail, MatchOdds, MatchStats, Standing
from ou25_pipeline.orchestration.mapping import (
    assign_standing_stage_labels,
    map_lineup_rows,
    map_match_row,
    map_match_team_stats_rows,
    map_odds_rows,
    map_standing_row,
)


def _standing(team_id: str, position: int, matches_played: int, group_label: str | None = None) -> Standing:
    return Standing.model_validate(
        {
            "team": {"id": team_id, "name": team_id},
            "position": position,
            "matches_played": matches_played,
            "group_label": group_label,
        }
    )


def test_assign_standing_stage_labels_splits_playoff_tables_on_position_reset():
    # Belgian Pro League real shape: regular season (16 teams, positions
    # 1-16) followed by 3 separate playoff tables, each restarting at
    # position 1, group_label null throughout. Without stage labels, the
    # same team appears twice with an identical (season_id, team_id,
    # group_label) key, which crashes the upsert (CardinalityViolation).
    standings = [
        _standing("tm_1", 1, 30),
        _standing("tm_2", 2, 30),
        _standing("tm_3", 3, 30),
        # Championship playoffs — position resets to 1.
        _standing("tm_3", 1, 40),
        _standing("tm_2", 2, 40),
        _standing("tm_1", 3, 40),
        # Relegation playoffs — position resets to 1 again.
        _standing("tm_9", 1, 36),
        _standing("tm_8", 2, 36),
    ]
    labels = assign_standing_stage_labels(standings)
    assert labels == ["", "", "", "stage2", "stage2", "stage2", "stage3", "stage3"]

    # The point of the fix: (team_id, group_label) is now unique per row.
    keys = [(s.team.id, label) for s, label in zip(standings, labels)]
    assert len(keys) == len(set(keys))


def test_assign_standing_stage_labels_passes_through_real_group_labels_unchanged():
    # A genuine group-stage tournament (World Cup groups A-L) shouldn't be
    # affected by the playoff-stage heuristic at all.
    standings = [
        _standing("tm_1", 1, 3, group_label="A"),
        _standing("tm_2", 1, 3, group_label="B"),
    ]
    assert assign_standing_stage_labels(standings) == ["A", "B"]


def test_map_standing_row_uses_the_passed_group_label():
    s = _standing("tm_1", 1, 30)
    row = map_standing_row("sn_1", 16, s, "stage2")
    assert row["group_label"] == "stage2"


def test_map_lineup_rows_dedupes_player_repeated_in_substitutes_list():
    # Real data: Liga MX Querétaro FC's substitutes list repeated 5 players
    # each twice — this crashed the match_lineup_players upsert
    # (CardinalityViolation on the match_id/team_id/player_id key).
    lineup = Lineup.model_validate(
        {
            "match_id": "mt_1",
            "confirmed": True,
            "away": {
                "formation": "4-4-2",
                "starting_xi": [{"id": "pl_1"}],
                "substitutes": [{"id": "pl_2"}, {"id": "pl_2"}, {"id": "pl_3"}],
            },
        }
    )
    _, player_rows = map_lineup_rows("mt_1", "tm_home", "tm_away", lineup)
    assert len(player_rows) == 3  # pl_1, pl_2 (once), pl_3 — not 4
    ids = [p["player_id"] for p in player_rows]
    assert ids.count("pl_2") == 1


def test_map_match_row_prefers_detail_score_for_half_time_goals():
    # The matches-list endpoint's score has no half_time_home/away — only
    # the single-match detail endpoint does. map_match_row must prefer
    # detail.score, not match.score, or HT goals silently come back None.
    base = {
        "id": "mt_1",
        "competition_id": "comp_1",
        "season_id": "sn_1",
        "utc_date": "2025-05-25T15:00:00Z",
        "home_team": {"id": "tm_1", "name": "Home"},
        "away_team": {"id": "tm_2", "name": "Away"},
        "status": "finished",
        "score": {"home": 0, "away": 2},
    }
    match = Match.model_validate(base)
    detail = MatchDetail.model_validate(
        {**base, "score": {"home": 0, "away": 2, "half_time_home": 0, "half_time_away": 1}}
    )
    row = map_match_row(match, detail, referee_id=None)
    assert row["home_goals_ht"] == 0
    assert row["away_goals_ht"] == 1


def test_odds_mapping_puts_opposite_sides_of_a_line_in_the_same_wide_row():
    odds = MatchOdds.model_validate(
        {
            "match_id": "mt_1",
            "bookmakers": [
                {
                    "bookmaker": "Bet365",
                    "markets": {
                        "match_odds": {
                            "home": {"opening": None, "last_seen": "1.800"},
                            "draw": {"opening": "3.500", "last_seen": "3.600"},
                            "away": {"opening": None, "last_seen": "4.500"},
                        },
                        "total_goals": {
                            "2.5": {
                                "over": {"opening": None, "last_seen": "1.570"},
                                "under": {"opening": None, "last_seen": "2.380"},
                            }
                        },
                        "btts": {
                            "yes": {"opening": None, "last_seen": "1.670"},
                            "no": {"opening": None, "last_seen": "2.100"},
                        },
                        # Not in the O/U 2.5 allowlist — must be dropped entirely.
                        "asian_handicap": {
                            "home": {"+1": {"opening": None, "last_seen": "1.980"}},
                            "away": {"-1": {"opening": None, "last_seen": "1.880"}},
                        },
                        "total_cards": {
                            "4.5": {"over": {"opening": None, "last_seen": "1.900"}}
                        },
                    },
                }
            ],
        }
    )
    rows = map_odds_rows("mt_1", odds)

    assert len(rows) == 1  # one row per bookmaker
    row = rows[0]
    assert row["bookmaker"] == "Bet365"

    # The exact thing a long table makes awkward: both sides of the same
    # line, directly on one row, no self-join.
    assert row["goals_2_5_over_close"] == 1.57
    assert row["goals_2_5_under_close"] == 2.38
    assert row["home_odds_close"] == 1.8
    assert row["draw_odds_open"] == 3.5
    assert row["btts_yes_odds_close"] == 1.67
    assert row["btts_no_odds_close"] == 2.1

    # Dropped markets never produce columns at all.
    assert not any(k.startswith("asian_handicap") or k.startswith("total_cards") for k in row)


def test_odds_mapping_drops_a_total_goals_line_outside_the_known_set():
    odds = MatchOdds.model_validate(
        {
            "match_id": "mt_1",
            "bookmakers": [
                {
                    "bookmaker": "Bet365",
                    "markets": {
                        "total_goals": {
                            "2.5": {"over": {"opening": None, "last_seen": "1.570"}},
                            "10.5": {"over": {"opening": None, "last_seen": "50.000"}},
                        }
                    },
                }
            ],
        }
    )
    rows = map_odds_rows("mt_1", odds)
    row = rows[0]
    assert row["goals_2_5_over_close"] == 1.57
    assert not any(k.startswith("goals_10_5") for k in row)


def test_odds_mapping_omits_bookmaker_row_with_no_relevant_markets():
    odds = MatchOdds.model_validate(
        {
            "match_id": "mt_1",
            "bookmakers": [
                {"bookmaker": "Bet365", "markets": {"total_cards": {"4.5": {"over": {"last_seen": "1.9"}}}}}
            ],
        }
    )
    assert map_odds_rows("mt_1", odds) == []


def test_odds_flattening_empty_when_no_odds():
    assert map_odds_rows("mt_1", None) == []


def test_odds_mapping_dedupes_repeated_bookmaker_entries():
    # The provider has shown duplicate list entries in two other endpoints
    # (playoff-stage standings, lineup substitutes) — a repeated bookmaker
    # name would hit the same (match_id, bookmaker) CardinalityViolation.
    odds = MatchOdds.model_validate(
        {
            "match_id": "mt_1",
            "bookmakers": [
                {
                    "bookmaker": "Bet365",
                    "markets": {"btts": {"yes": {"opening": None, "last_seen": "1.670"}}},
                },
                {
                    "bookmaker": "Bet365",
                    "markets": {"btts": {"no": {"opening": None, "last_seen": "2.100"}}},
                },
            ],
        }
    )
    rows = map_odds_rows("mt_1", odds)
    assert len(rows) == 1
    assert rows[0]["btts_yes_odds_close"] == 1.67
    assert rows[0]["btts_no_odds_close"] == 2.1


def test_flatten_odds_tree_handles_selection_then_line_nesting():
    # asian_handicap nests the opposite way to total_goals: selection -> line
    # rather than line -> selection. Not in the O/U 2.5 allowlist anymore,
    # but the flattener must still handle whatever order a market throws at
    # it without assuming one — exercised here directly.
    from ou25_pipeline.orchestration.mapping import _flatten_odds_tree, _split_selection_and_line

    tree = {
        "home": {"+1": {"opening": None, "last_seen": "1.980"}},
        "away": {"-1": {"opening": None, "last_seen": "1.880"}},
    }
    leaves = list(_flatten_odds_tree(tree))
    assert len(leaves) == 2
    selection, line = _split_selection_and_line(leaves[0][0])
    assert (selection, line) in {("home", 1.0), ("away", -1.0)}


def test_match_team_stats_pivots_overview_dict_into_home_away_rows():
    stats = MatchStats.model_validate(
        {
            "match_id": "mt_1",
            "overview": {
                "total_shots": {"all": {"home": 13, "away": 20}},
                "shots_on_target": {"all": {"home": 3, "away": 5}},
                "ball_possession": {"all": {"home": 47, "away": 53}},
                "corner_kicks": {"all": {"home": 1, "away": 6}},
                "expected_goals": {"all": {"home": 1.28, "away": 2.99}},
            },
        }
    )
    rows = map_match_team_stats_rows("mt_1", "tm_home", "tm_away", stats, shotmap=None)
    home_row = next(r for r in rows if r["team_id"] == "tm_home")
    assert home_row["shots_total"] == 13
    assert home_row["xg"] == 1.28
    away_row = next(r for r in rows if r["team_id"] == "tm_away")
    assert away_row["possession_pct"] == 53


def test_match_team_stats_handles_explicit_null_stat_category():
    stats = MatchStats.model_validate(
        {
            "match_id": "mt_1",
            "overview": {
                "total_shots": {"all": {"home": 13, "away": 20}},
                "expected_goals": None,
            },
        }
    )
    rows = map_match_team_stats_rows("mt_1", "tm_home", "tm_away", stats, shotmap=None)
    home_row = next(r for r in rows if r["team_id"] == "tm_home")
    assert home_row["shots_total"] == 13
    assert home_row["xg"] is None


def test_match_team_stats_falls_back_to_shotmap_sum_when_expected_goals_missing():
    stats = MatchStats.model_validate({"match_id": "mt_1", "overview": {}})
    shotmap = [
        {"team_id": "tm_home", "expected_goals": 0.3},
        {"team_id": "tm_home", "expected_goals": 0.4},
    ]
    rows = map_match_team_stats_rows("mt_1", "tm_home", "tm_away", stats, shotmap)
    home_row = next(r for r in rows if r["team_id"] == "tm_home")
    assert home_row["xg"] == 0.7


def test_match_team_stats_skips_side_with_no_data_at_all():
    stats = MatchStats.model_validate({"match_id": "mt_1", "overview": {}})
    rows = map_match_team_stats_rows("mt_1", "tm_home", "tm_away", stats, shotmap=None)
    assert rows == []
