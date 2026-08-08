import logging
from dataclasses import dataclass

from sqlalchemy import Engine

from ou25_pipeline.api import endpoints
from ou25_pipeline.api.client import QuotaExceededError, StatsAPIClient
from ou25_pipeline.models.schemas import Competition, Match, Season, Team
from ou25_pipeline.orchestration import mapping
from ou25_pipeline.orchestration.checkpoint import Checkpoint
from ou25_pipeline.storage import tables
from ou25_pipeline.storage.writer import upsert

logger = logging.getLogger(__name__)


@dataclass
class BackfillSummary:
    matches_total: int = 0
    matches_processed: int = 0
    matches_skipped_done: int = 0
    matches_failed: int = 0
    quota_exceeded: bool = False


def resolve_competition(client: StatsAPIClient, competition: str) -> Competition:
    """Accepts either a raw `comp_...` id or a free-text name search
    (matched case-insensitively against the provider's `search` filter)."""
    if competition.startswith("comp_"):
        comp = endpoints.get_competition(client, competition)
        if comp is None:
            raise ValueError(f"Competition id {competition!r} not found")
        return comp

    candidates = endpoints.list_competitions(client, search=competition)
    if not candidates:
        raise ValueError(f"No competition found matching {competition!r}")

    # The provider's `search` does a substring match against name, so
    # "Premier League" also returns "Canadian Premier League", "Egyptian
    # Premier League", etc. Prefer an exact case-insensitive name match
    # over raising ambiguous — only fall back to an error if that's not
    # unique either.
    exact = [c for c in candidates if c.name.lower() == competition.lower()]
    if len(exact) == 1:
        return exact[0]
    if len(candidates) == 1:
        return candidates[0]

    options = ", ".join(f"{c.name} ({c.id})" for c in candidates)
    raise ValueError(f"Ambiguous competition {competition!r} — candidates: {options}")


def resolve_season(client: StatsAPIClient, competition_id: str, season: str) -> Season:
    """`season` is matched against `start_year` first (most robust — e.g.
    "2024-25", "24/25", and "2024" should all find start_year=2024), falling
    back to a substring match against the season's `year` code or `name`."""
    seasons = endpoints.list_seasons(client, competition_id)
    target_year = _first_four_digit_year(season)

    if target_year is not None:
        matches = [s for s in seasons if s.start_year == target_year]
        if len(matches) == 1:
            return matches[0]

    for s in seasons:
        if season.lower() in s.year.lower() or season.lower() in s.name.lower():
            return s

    names = ", ".join(s.name for s in seasons)
    raise ValueError(f"No season matching {season!r} — available: {names}")


def _first_four_digit_year(text: str) -> int | None:
    """Finds the first run of 4 consecutive digits, e.g. "2024" out of
    "2024-25". Inputs like "24/25" have no such run and return None —
    resolve_season falls back to matching against the season's `year` code."""
    run = ""
    for ch in text:
        run = run + ch if ch.isdigit() else ""
        if len(run) == 4:
            return int(run)
    return None


def run_backfill(
    client: StatsAPIClient, engine: Engine, competition: str, season: str
) -> BackfillSummary:
    checkpoint = Checkpoint(engine)
    summary = BackfillSummary()

    comp = resolve_competition(client, competition)
    season_obj = resolve_season(client, comp.id, season)

    with engine.begin() as conn:
        upsert(conn, tables.competitions, [mapping.map_competition_row(comp)])
        upsert(conn, tables.seasons, [mapping.map_season_row(comp.id, season_obj)])

    standings = endpoints.get_standings(client, comp.id, season_obj.id)
    # Distinct teams, not row count — a league with playoff-stage tables
    # (see assign_standing_stage_labels) repeats teams across multiple rows.
    total_teams = len({s.team.id for s in standings})
    stage_labels = mapping.assign_standing_stage_labels(standings)

    # Same duplication as standings (playoff-stage tables repeat teams) — a
    # dict keyed by team_id collapses to one row per distinct team, since a
    # single INSERT...ON CONFLICT statement can't target the same row twice.
    distinct_teams = {s.team.id: s.team for s in standings}.values()

    with engine.begin() as conn:
        upsert(conn, tables.teams, [mapping.map_team_row(t.id, t.name, None) for t in distinct_teams])
        upsert(
            conn,
            tables.standings,
            [
                mapping.map_standing_row(season_obj.id, total_teams, s, label)
                for s, label in zip(standings, stage_labels)
            ],
        )

    matches = endpoints.list_matches(
        client, competition_id=comp.id, season_id=season_obj.id, status="finished"
    )
    summary.matches_total = len(matches)
    logger.info(
        "Fetched match list",
        extra={
            "extra_fields": {
                "count": len(matches),
                "competition_id": comp.id,
                "season_id": season_obj.id,
            }
        },
    )

    team_cache: dict[str, Team | None] = {}

    for match in matches:
        if checkpoint.is_done("match", match.id):
            summary.matches_skipped_done += 1
            continue
        try:
            _process_match(client, engine, match, team_cache)
            checkpoint.mark_done("match", match.id, comp.id, season_obj.id)
            summary.matches_processed += 1
        except QuotaExceededError as exc:
            # Every remaining request would hit the same wall — stop issuing
            # them entirely rather than marking hundreds of matches "failed"
            # for a reason that has nothing to do with those matches.
            logger.critical(
                "Monthly API quota exceeded — stopping run early",
                extra={"extra_fields": {"match_id": match.id}},
            )
            checkpoint.mark_failed("match", match.id, str(exc), comp.id, season_obj.id)
            summary.matches_failed += 1
            summary.quota_exceeded = True
            break
        except Exception as exc:  # noqa: BLE001 - one bad match must not kill the run
            logger.exception(
                "Match processing failed", extra={"extra_fields": {"match_id": match.id}}
            )
            checkpoint.mark_failed("match", match.id, str(exc), comp.id, season_obj.id)
            summary.matches_failed += 1

    logger.info("Backfill complete", extra={"extra_fields": vars(summary)})
    return summary


def _get_team_cached(
    client: StatsAPIClient, team_cache: dict[str, Team | None], team_id: str
) -> Team | None:
    if team_id not in team_cache:
        team_cache[team_id] = endpoints.get_team(client, team_id)
    return team_cache[team_id]


def _process_match(
    client: StatsAPIClient, engine: Engine, match: Match, team_cache: dict[str, Team | None]
) -> None:
    detail = endpoints.get_match_detail(client, match.id)
    referee = endpoints.get_match_referee(client, match.id)
    stats = endpoints.get_match_stats(client, match.id)
    odds = endpoints.get_match_odds(client, match.id) if match.odds_available else None
    lineups = endpoints.get_match_lineups(client, match.id)
    timeline = endpoints.get_match_timeline(client, match.id)
    shotmap = endpoints.get_match_shotmap(client, match.id) if match.xg_available else None

    home_team_detail = _get_team_cached(client, team_cache, match.home_team.id)
    away_team_detail = _get_team_cached(client, team_cache, match.away_team.id)

    match_row = mapping.map_match_row(match, detail, referee.id if referee else None)
    team_rows = [
        mapping.map_team_row(match.home_team.id, match.home_team.name, home_team_detail),
        mapping.map_team_row(match.away_team.id, match.away_team.name, away_team_detail),
    ]
    team_stats_rows = mapping.map_match_team_stats_rows(
        match.id, match.home_team.id, match.away_team.id, stats, shotmap
    )
    odds_rows = mapping.map_odds_rows(match.id, odds)
    lineup_rows, player_rows = mapping.map_lineup_rows(
        match.id, match.home_team.id, match.away_team.id, lineups
    )
    event_rows = mapping.map_event_rows(match.id, timeline)

    # One transaction per match: either all of a match's satellite data
    # lands, or none does — keeps the checkpoint's "done" status meaningful.
    with engine.begin() as conn:
        if referee is not None:
            upsert(conn, tables.referees, [mapping.map_referee_row(referee)])
        upsert(conn, tables.teams, team_rows)
        upsert(conn, tables.matches, [match_row])
        upsert(conn, tables.match_team_stats, team_stats_rows)
        upsert(conn, tables.match_odds, odds_rows)
        upsert(conn, tables.match_lineups, lineup_rows)
        upsert(conn, tables.match_lineup_players, player_rows)
        upsert(conn, tables.match_events, event_rows)
