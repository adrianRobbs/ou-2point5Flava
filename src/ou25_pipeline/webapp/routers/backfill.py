"""`/api/backfill/*` — coverage reporting and on-demand backfill triggering.

The only authenticated routes anywhere in this app. Everything under
`/api/predictions` is deliberately public (it's just a read-only display);
this router costs real money per trigger (spins up a paid Render Job) and
would otherwise sit at a guessable, unauthenticated URL, so it is gated on
a single shared-secret header instead.
"""

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import Engine

from ou25_pipeline.api.client import StatsAPIClient
from ou25_pipeline.config import Settings, get_settings
from ou25_pipeline.orchestration import coverage
from ou25_pipeline.storage.db import get_engine
from ou25_pipeline.webapp import render_client
from ou25_pipeline.webapp.render_client import RenderNotConfiguredError
from ou25_pipeline.webapp.routers.predictions import engine_dependency

router = APIRouter(prefix="/api/backfill")


def settings_dependency() -> Settings:
    return get_settings()


def require_admin_token(
    x_admin_token: str | None = Header(default=None),
    settings: Settings = Depends(settings_dependency),
) -> None:
    """403s if the header is missing/wrong, or if the token was never
    configured at all — an unset `BACKFILL_ADMIN_TOKEN` must lock the
    route out, not accidentally leave it open."""
    if not settings.backfill_admin_token or x_admin_token != settings.backfill_admin_token:
        raise HTTPException(status_code=403, detail="Missing or invalid admin token.")


def api_client(settings: Settings) -> StatsAPIClient:
    return StatsAPIClient(
        base_url=settings.thestatsapi_base_url,
        api_key=settings.thestatsapi_key,
        requests_per_minute=settings.thestatsapi_rate_limit_per_min,
    )


@router.get("/competitions", dependencies=[Depends(require_admin_token)])
def list_competitions(engine: Engine = Depends(engine_dependency)) -> list[dict]:
    return coverage.list_tracked_competitions(engine)


@router.get("/competitions/{competition_id}/seasons", dependencies=[Depends(require_admin_token)])
def list_seasons(
    competition_id: str,
    engine: Engine = Depends(engine_dependency),
    settings: Settings = Depends(settings_dependency),
) -> list[dict]:
    with api_client(settings) as client:
        return coverage.list_seasons_with_coverage(client, engine, competition_id)


@router.get(
    "/competitions/{competition_id}/seasons/{season_id}/coverage",
    dependencies=[Depends(require_admin_token)],
)
def get_coverage(
    competition_id: str,
    season_id: str,
    engine: Engine = Depends(engine_dependency),
    settings: Settings = Depends(settings_dependency),
) -> dict:
    with api_client(settings) as client:
        return coverage.check_season_coverage(client, engine, competition_id, season_id)


@router.post(
    "/competitions/{competition_id}/seasons/{season_id}/sync",
    dependencies=[Depends(require_admin_token)],
)
def trigger_sync(
    competition_id: str,
    season_id: str,
    year: str,
    settings: Settings = Depends(settings_dependency),
) -> dict:
    """`year` (e.g. "24/25") comes from the client, which already has it
    from the `/seasons` response — the exact same string `resolve_season`'s
    existing substring match already knows how to resolve, so this assembles
    only the CLI command a human would type by hand, nothing new.
    """
    command = f"uv run ou25-pipeline backfill --competition {competition_id} --season {year}"
    try:
        return render_client.trigger_job(settings.render_api_key, settings.render_service_id, command)
    except RenderNotConfiguredError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc


@router.get("/jobs/{job_id}", dependencies=[Depends(require_admin_token)])
def job_status(job_id: str, settings: Settings = Depends(settings_dependency)) -> dict:
    try:
        return render_client.get_job_status(settings.render_api_key, job_id)
    except RenderNotConfiguredError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
