"""`/api/sync/*` — manual "run discover-fixtures / refresh-odds right now"
buttons, for while no Cron Job is deployed (see render.yaml: the web
service is on Render's free tier, which has no free option for Cron Jobs
at all, so the scheduled versions of these are commented out there).

Runs the exact same `orchestration.daily_sync` functions the CLI commands
use, synchronously, inside the request — not a Render Job like
`routers/backfill.py`'s backfill sync, since a full historical backfill and
"catch up on what's changed since the last click" are very different in
scope. A `backfill` run can take 10-40+ minutes; discovering or refreshing
the already-tracked competitions' near-term fixtures is bounded by design
(status='scheduled', a day-window filter) and expected to finish within an
ordinary request. If that stops holding — many more tracked leagues, a
slow provider day — this is the first place to revisit, not something to
assume forever.

Behind the same admin-token gate as `routers/backfill.py`, reusing its
dependency rather than a second copy: these still make live API calls and
write to the DB, which is enough reason to keep it out of anonymous reach
even though nothing here spends money the way a Job does.
"""

from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy import Engine

from ou25_pipeline.config import Settings
from ou25_pipeline.orchestration.catalog import list_tracked_competition_ids
from ou25_pipeline.orchestration.daily_sync import discover_fixtures_for_competitions, refresh_odds_for_competitions
from ou25_pipeline.webapp.routers.backfill import api_client, require_admin_token, settings_dependency
from ou25_pipeline.webapp.routers.predictions import engine_dependency

router = APIRouter(prefix="/api/sync")


@router.post("/discover", dependencies=[Depends(require_admin_token)])
def discover(
    max_days_ahead: int = 2,
    engine: Engine = Depends(engine_dependency),
    settings: Settings = Depends(settings_dependency),
) -> dict:
    with api_client(settings) as client:
        result = discover_fixtures_for_competitions(
            client, engine, list_tracked_competition_ids(engine), max_days_ahead
        )
    return asdict(result)


@router.post("/refresh", dependencies=[Depends(require_admin_token)])
def refresh(
    engine: Engine = Depends(engine_dependency), settings: Settings = Depends(settings_dependency)
) -> dict:
    with api_client(settings) as client:
        result = refresh_odds_for_competitions(client, engine, list_tracked_competition_ids(engine))
    return asdict(result)
