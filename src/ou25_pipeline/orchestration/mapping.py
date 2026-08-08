"""Pure functions: parsed API models -> flat dict rows ready for `storage.writer.upsert`.
Kept separate from `pipeline.py` so the flattening logic (especially the
odds tree and the stats pivot) can be unit tested without a live API or DB.
"""

from typing import Any

from ou25_pipeline.models.schemas import (
    Competition,
    Lineup,
    Match,
    MatchDetail,
    MatchOdds,
    MatchStats,
    Referee,
    Season,
    Standing,
    Team,
    Timeline,
)

# The provider returns far more markets than an O/U 2.5 model needs
# (asian_handicap, total_cards, draw_no_bet, double_chance,
# first_half_result, first_team_to_score). Keep only the ones in the
# approved Phase 2 schema: total_goals (all lines — the shape of the market's
# implied goals distribution, not just the 2.5 line), match_odds (1X2, a
# margin/competitiveness proxy), and btts (correlates directly with total
# goals). Everything else is either redundant with these or unrelated to
# goals entirely.
_RELEVANT_MARKETS = {"total_goals", "match_odds", "btts"}

# overview stat name -> match_team_stats column
_STAT_KEY_TO_COLUMN = {
    "total_shots": "shots_total",
    "shots_on_target": "shots_on_target",
    "ball_possession": "possession_pct",
    "corner_kicks": "corners",
    "expected_goals": "xg",
}


def map_competition_row(c: Competition) -> dict[str, Any]:
    return {"competition_id": c.id, "name": c.name, "country": c.country, "type": c.type}


def map_season_row(competition_id: str, s: Season) -> dict[str, Any]:
    return {
        "season_id": s.id,
        "competition_id": competition_id,
        "name": s.name,
        "year": s.year,
        "start_year": s.start_year,
        "end_year": s.end_year,
        "is_current": s.is_current,
    }


def map_team_row(team_id: str, name: str, team_detail: Team | None) -> dict[str, Any]:
    return {
        "team_id": team_id,
        "name": name,
        "country": team_detail.country if team_detail else None,
    }


def map_referee_row(r: Referee) -> dict[str, Any]:
    career = r.career
    return {
        "referee_id": r.id,
        "name": r.name,
        "country": r.country,
        "career_games": career.games if career else None,
        "career_yellow": career.yellow_cards if career else None,
        "career_red": career.red_cards if career else None,
        "career_yellow_red": career.yellow_red_cards if career else None,
    }


def assign_standing_stage_labels(standings: list[Standing]) -> list[str]:
    """Some leagues (e.g. Belgian Pro League) return a regular-season table
    followed by separate playoff-stage tables (Championship/Europe/
    Relegation playoffs) concatenated in one response, with every row's
    `group_label` set to null — so the same team appears multiple times
    with an otherwise-identical (season_id, team_id, group_label) key. A
    real `group_label` (tournament groups A-L) passes through unchanged;
    for null-labeled rows, a new synthetic stage is opened whenever
    `position` resets to a value at or below the previous row's — which is
    exactly what happens at the boundary between the regular season and
    each playoff table."""
    labels = []
    stage = 1
    prev_position: int | None = None
    for s in standings:
        if s.group_label:
            labels.append(s.group_label)
            prev_position = None
            continue
        if prev_position is not None and s.position is not None and s.position <= prev_position:
            stage += 1
        labels.append("" if stage == 1 else f"stage{stage}")
        prev_position = s.position
    return labels


def map_standing_row(season_id: str, total_teams: int, s: Standing, group_label: str) -> dict[str, Any]:
    return {
        "season_id": season_id,
        "team_id": s.team.id,
        "group_label": group_label,
        "position": s.position,
        "total_teams": total_teams,
        "matches_played": s.matches_played,
        "wins": s.wins,
        "draws": s.draws,
        "losses": s.losses,
        "goals_for": s.goals_for,
        "goals_against": s.goals_against,
        "goal_difference": s.goal_difference,
        "points": s.points,
    }


def map_match_row(match: Match, detail: MatchDetail | None, referee_id: str | None) -> dict[str, Any]:
    # The list endpoint's score omits half_time_home/away — only the
    # single-match detail endpoint includes them. Prefer detail's score
    # when we have it; fall back to the list-level one otherwise (e.g. the
    # detail fetch 404'd).
    score = (detail.score if detail else None) or match.score
    venue = detail.venue if detail else None
    resolved_referee_id = referee_id or (detail.referee.id if detail and detail.referee else None)
    return {
        "match_id": match.id,
        "competition_id": match.competition_id,
        "season_id": match.season_id,
        "matchday": match.matchday,
        "stage_name": match.stage_name,
        "group_label": match.group_label,
        "kickoff_utc": match.utc_date,
        "home_team_id": match.home_team.id,
        "away_team_id": match.away_team.id,
        "venue_name": venue.name if venue else None,
        "venue_city": venue.city if venue else None,
        "referee_id": resolved_referee_id,
        "home_goals_ft": score.home if score else None,
        "away_goals_ft": score.away if score else None,
        "home_goals_ht": score.half_time_home if score else None,
        "away_goals_ht": score.half_time_away if score else None,
        "status": match.status,
        "odds_available": match.odds_available,
        "xg_available": match.xg_available,
    }


def map_match_team_stats_rows(
    match_id: str,
    home_team_id: str,
    away_team_id: str,
    stats: MatchStats | None,
    shotmap: list[dict] | None,
) -> list[dict[str, Any]]:
    """`stats.overview` is keyed by stat name (total_shots, ball_possession,
    expected_goals, ...), each holding an all/first_half/second_half split of
    {home, away} — pivot that into one row per side with our fixed columns.
    """
    xg_by_team: dict[str, float] = {}
    for shot in shotmap or []:
        team_id = shot.get("team_id")
        xg = shot.get("expected_goals")
        if team_id is not None and xg is not None:
            xg_by_team[team_id] = xg_by_team.get(team_id, 0.0) + float(xg)

    overview = stats.overview if stats else {}
    values: dict[str, dict[str, float | None]] = {}
    for stat_key, column in _STAT_KEY_TO_COLUMN.items():
        split = overview.get(stat_key)
        pair = split.all if split else None
        values[column] = {
            "home": pair.home if pair else None,
            "away": pair.away if pair else None,
        }

    rows = []
    for side, team_id in (("home", home_team_id), ("away", away_team_id)):
        xg = values["xg"][side]
        if xg is None:
            xg = xg_by_team.get(team_id)
        row = {
            "match_id": match_id,
            "team_id": team_id,
            "side": side,
            "shots_total": values["shots_total"][side],
            "shots_on_target": values["shots_on_target"][side],
            "possession_pct": values["possession_pct"][side],
            "corners": values["corners"][side],
            "xg": xg,
        }
        if any(v is not None for k, v in row.items() if k not in ("match_id", "team_id", "side")):
            rows.append(row)
    return rows


def _try_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _flatten_odds_tree(node: dict, path: tuple[str, ...] = ()):
    """Walks an arbitrarily-nested market dict down to its {opening,
    last_seen} leaves, yielding (path, opening, last_seen). Nesting order
    varies by market — total_goals is line->selection, asian_handicap is
    selection->line, match_odds/btts have no line at all — so this doesn't
    assume an order, just recurses until it hits a leaf."""
    if "opening" in node and "last_seen" in node:
        yield path, node.get("opening"), node.get("last_seen")
        return
    for key, value in node.items():
        if isinstance(value, dict):
            yield from _flatten_odds_tree(value, path + (key,))


def _split_selection_and_line(path: tuple[str, ...]) -> tuple[str, float | None]:
    """Exactly one path segment is usually numeric (the line, e.g. '2.5' or
    '+1') and the rest are categorical (the selection, e.g. 'over', 'home').
    Falls back to joining the whole path as the selection if that doesn't
    hold — keeps unfamiliar markets from crashing the pipeline."""
    if len(path) == 1:
        return path[0], None
    numeric = [(i, _try_float(p)) for i, p in enumerate(path)]
    numeric = [(i, v) for i, v in numeric if v is not None]
    if len(numeric) == 1:
        idx, line = numeric[0]
        selection = "/".join(p for i, p in enumerate(path) if i != idx)
        return selection, line
    return "/".join(path), None


# The fixed, empirically observed set of total_goals lines — matches the
# columns in db/schema.sql / storage.tables.match_odds. A line outside this
# set is dropped (see _resolve_odds_column) rather than crashing, since
# there's no column to put it in.
TOTAL_GOALS_LINES = {0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5}


def _line_column_key(line: float) -> str:
    return str(line).replace(".", "_")


def _resolve_odds_column(market: str, selection: str, line: float | None) -> str | None:
    """market/selection/line -> the wide column *prefix* (caller appends
    _open/_close), or None if this combination isn't one we store."""
    if market == "match_odds" and selection in ("home", "draw", "away"):
        return f"{selection}_odds"
    if market == "btts" and selection in ("yes", "no"):
        return f"btts_{selection}_odds"
    if market == "total_goals" and selection in ("over", "under") and line in TOTAL_GOALS_LINES:
        return f"goals_{_line_column_key(line)}_{selection}"
    return None


def map_odds_rows(match_id: str, odds: MatchOdds | None) -> list[dict[str, Any]]:
    """One row per bookmaker, opposite sides of the same line as columns
    side by side (goals_2_5_over_close / goals_2_5_under_close, etc.) rather
    than a long table — a long table needs a self-join to answer "what's the
    other side of the line I'm looking at," which is almost always what
    training-data consumption actually wants.

    Keyed by bookmaker name (not appended to a plain list): the provider has
    already shown duplicate entries within a single list in two other
    endpoints (playoff-stage standings, lineup substitutes), and a repeated
    bookmaker name here would hit the exact same CardinalityViolation on the
    (match_id, bookmaker) upsert. A later duplicate's fields overwrite the
    earlier one's, matching how the DB upsert itself would resolve it.
    """
    rows_by_bookmaker: dict[str, dict[str, Any]] = {}
    for bookmaker in odds.bookmakers if odds else []:
        row = rows_by_bookmaker.setdefault(
            bookmaker.bookmaker, {"match_id": match_id, "bookmaker": bookmaker.bookmaker}
        )
        for market_name, market_body in bookmaker.markets.items():
            if market_name not in _RELEVANT_MARKETS:
                continue
            for path, opening, last_seen in _flatten_odds_tree(market_body):
                selection, line = _split_selection_and_line(path)
                column = _resolve_odds_column(market_name, selection, line)
                if column is None:
                    continue
                row[f"{column}_open"] = _try_float(opening)
                row[f"{column}_close"] = _try_float(last_seen)
    return [row for row in rows_by_bookmaker.values() if len(row) > 2]


def map_lineup_rows(
    match_id: str, home_team_id: str, away_team_id: str, lineup: Lineup | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lineup_rows: list[dict[str, Any]] = []
    player_rows: list[dict[str, Any]] = []
    if lineup is None:
        return lineup_rows, player_rows

    for side, team_id, team_lineup in (
        ("home", home_team_id, lineup.home),
        ("away", away_team_id, lineup.away),
    ):
        if team_lineup is None:
            continue
        lineup_rows.append(
            {
                "match_id": match_id,
                "team_id": team_id,
                "side": side,
                "confirmed": lineup.confirmed,
                "formation": team_lineup.formation,
            }
        )
        # The provider's own lineup data can list the same player twice in
        # one squad list (seen live: a Liga MX substitutes list repeating 5
        # players) — dedupe per (team_id, player_id) or the upsert's
        # multi-row INSERT hits the same CardinalityViolation as the
        # standings/teams duplication bugs. is_starter=True wins on
        # conflict, though a same-list duplicate is identical either way.
        team_players: dict[str, bool] = {}
        for player in team_lineup.starting_xi:
            team_players[player.id] = True
        for player in team_lineup.substitutes:
            team_players.setdefault(player.id, False)
        for player_id, is_starter in team_players.items():
            player_rows.append(
                {
                    "match_id": match_id,
                    "team_id": team_id,
                    "player_id": player_id,
                    "is_starter": is_starter,
                }
            )
    return lineup_rows, player_rows


def map_event_rows(match_id: str, timeline: Timeline | None) -> list[dict[str, Any]]:
    if timeline is None:
        return []
    return [
        {
            "match_id": match_id,
            "event_seq": event.sequence if event.sequence is not None else i,
            "minute": event.minute,
            "event_type": event.type,
            "team_id": event.team.id if event.team else None,
            "player_id": event.player.id if event.player else None,
            "detail": None,
        }
        for i, event in enumerate(timeline.events)
    ]
