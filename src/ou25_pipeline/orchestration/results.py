"""Syncs a single match's final result into `matches`, for fixtures the
live decision engine discovered (market/persistence.py) whose kickoff has
already passed.

Deliberately not a live/in-play poller. This project only ever bets
pre-match, so a result only needs checking on the same cadence odds are
refreshed — once kickoff has passed, `refresh-odds` calls this for any
fixture still missing a finished result; if the match is still in progress,
it is simply checked again on the next scheduled run.
"""

from sqlalchemy import Engine

from ou25_pipeline.api import endpoints
from ou25_pipeline.api.client import StatsAPIClient
from ou25_pipeline.orchestration import mapping
from ou25_pipeline.orchestration.registration import ensure_fixture_registered
from ou25_pipeline.storage import tables
from ou25_pipeline.storage.writer import upsert


def sync_finished_result(client: StatsAPIClient, engine: Engine, match_id: str) -> bool:
    """Returns whether a finished result was recorded this call — false
    covers both "fetch failed" and "still in progress, try again next
    refresh," which is fine: the caller doesn't need to tell those apart,
    only whether to expect the result to show up now or later.
    """
    detail = endpoints.get_match_detail(client, match_id)
    if detail is None or detail.status != "finished":
        return False

    # A live-discovered fixture may never have gone through `backfill` —
    # same FK chain `matches` has always required, satisfied the same way
    # `backfill` already satisfies it, just triggered here instead.
    ensure_fixture_registered(
        engine, client, detail.competition_id, detail.season_id,
        detail.home_team.id, detail.away_team.id, detail.home_team.name, detail.away_team.name,
    )

    referee = endpoints.get_match_referee(client, match_id)
    match_row = mapping.map_match_row(detail, detail, referee.id if referee else None)
    with engine.begin() as conn:
        # `matches.referee_id` is a FK — the referee must land first, same
        # order pipeline.py's backfill uses. Found live: a live-discovered
        # match reaching 'finished' referenced a referee never backfilled,
        # raising ForeignKeyViolation on every refresh-odds run for that
        # competition until fixed.
        if referee is not None:
            upsert(conn, tables.referees, [mapping.map_referee_row(referee)])
        upsert(conn, tables.matches, [match_row])
    return True
