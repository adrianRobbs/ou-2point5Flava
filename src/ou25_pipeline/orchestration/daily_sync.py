"""Shared orchestration for the two everyday operations — discovering
upcoming fixtures and refreshing odds/results — used by both `cli.py`'s
`discover-fixtures`/`refresh-odds` commands (meant for a scheduled cron)
and the webapp's manual sync buttons (`webapp/routers/sync.py`, meant for
right now, while the free-tier web service has no Cron Job available at
all — see render.yaml). One implementation, two callers, so a cron re-enabled
later and a manual click today can never quietly drift apart.
"""

from dataclasses import dataclass, field

from sqlalchemy import Engine

from ou25_pipeline.api.client import StatsAPIClient
from ou25_pipeline.market.live import classify_matches, list_upcoming_matches
from ou25_pipeline.market.persistence import (
    discover_fixtures,
    matches_awaiting_result,
    matches_needing_refresh,
    upsert_refreshed,
)
from ou25_pipeline.orchestration.registration import ensure_fixture_registered
from ou25_pipeline.orchestration.results import sync_finished_result


@dataclass
class DiscoverResult:
    total_discovered: int = 0
    per_competition: dict[str, int] = field(default_factory=dict)


def discover_fixtures_for_competitions(
    client: StatsAPIClient,
    engine: Engine,
    competition_ids: list[str],
    max_days_ahead: int | None = None,
) -> DiscoverResult:
    """Lists upcoming, priced fixtures for each competition, registers any
    unknown competition/season/team first (see `orchestration.registration`
    for why that's needed at all), and inserts 'pending' placeholders.
    """
    result = DiscoverResult()
    for competition_id in competition_ids:
        upcoming = list_upcoming_matches(client, competition_id, max_days_ahead=max_days_ahead)
        for match in upcoming:
            ensure_fixture_registered(
                engine, client, match.competition_id, match.season_id,
                match.home_team_id, match.away_team_id, match.home_team, match.away_team,
            )
        inserted = discover_fixtures(engine, upcoming)
        result.total_discovered += inserted
        if upcoming:
            result.per_competition[competition_id] = inserted
    return result


@dataclass
class RefreshResult:
    total_refreshed: int = 0
    total_backed: int = 0
    total_results_synced: int = 0
    per_competition: dict[str, dict] = field(default_factory=dict)


def refresh_odds_for_competitions(
    client: StatsAPIClient, engine: Engine, competition_ids: list[str]
) -> RefreshResult:
    """Re-fetches odds for every 'pending'/not-yet-kicked-off prediction, then
    syncs the final result for any fixture whose kickoff has already passed
    but has no recorded result yet — the same combined step
    `refresh-odds` has run since results were folded into its cadence rather
    than given a separate live-polling job.
    """
    result = RefreshResult()
    for competition_id in competition_ids:
        pending = matches_needing_refresh(engine, [competition_id])
        refreshed = backed = 0
        if pending:
            recommendations = classify_matches(client, engine, pending)
            refreshed = upsert_refreshed(engine, recommendations)
            backed = sum(1 for r in recommendations if r.decision.call == "BACK_FAVOURITE")
            result.total_refreshed += refreshed
            result.total_backed += backed

        awaiting_result = matches_awaiting_result(engine, [competition_id])
        synced = 0
        if awaiting_result:
            synced = sum(sync_finished_result(client, engine, match_id) for match_id in awaiting_result)
            result.total_results_synced += synced

        if refreshed or synced:
            result.per_competition[competition_id] = {
                "refreshed": refreshed,
                "backed": backed,
                "results_synced": synced,
                "awaiting_result": len(awaiting_result),
            }
    return result
