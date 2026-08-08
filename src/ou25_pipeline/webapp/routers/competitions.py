"""`/api/competitions/*` — the full provider catalog (~150 competitions) and
which ones we've deliberately chosen to track.

Behind the same admin-token gate as `routers/backfill.py`/`routers/sync.py`
(reusing its dependency, not a second copy): this writes tracking decisions
and makes live API calls, the same bar the other two routers use.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Engine

from ou25_pipeline.config import Settings
from ou25_pipeline.orchestration import catalog
from ou25_pipeline.webapp.routers.backfill import api_client, require_admin_token, settings_dependency
from ou25_pipeline.webapp.routers.predictions import engine_dependency

router = APIRouter(prefix="/api/competitions")


@router.get("", dependencies=[Depends(require_admin_token)])
def list_competitions(engine: Engine = Depends(engine_dependency)) -> list[dict]:
    return catalog.list_all_competitions(engine)


@router.post("/sync-catalog", dependencies=[Depends(require_admin_token)])
def sync_catalog(
    engine: Engine = Depends(engine_dependency), settings: Settings = Depends(settings_dependency)
) -> dict:
    with api_client(settings) as client:
        synced = catalog.sync_competition_catalog(client, engine)
    return {"synced": synced}


class TrackRequest(BaseModel):
    tier: int | None = None


@router.post("/{competition_id}/track", dependencies=[Depends(require_admin_token)])
def track(competition_id: str, body: TrackRequest, engine: Engine = Depends(engine_dependency)) -> dict:
    try:
        catalog.set_competition_tracked(engine, competition_id, tracked=True, tier=body.tier)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"competition_id": competition_id, "is_tracked": True, "tier": body.tier}
