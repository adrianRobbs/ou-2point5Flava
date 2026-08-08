"""FastAPI entrypoint. One Render web service serves both the API and the
built frontend — same origin, no CORS configuration needed, one URL.

Route registration order matters here: the `/api` router is included
*before* the frontend static mount at `/`, so an `/api/*` request is matched
by the API router and never falls through to the SPA catch-all. Starlette
checks routes in registration order, so this would silently break if the
mount were added first.
"""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ou25_pipeline.webapp.routers.backfill import router as backfill_router
from ou25_pipeline.webapp.routers.competitions import router as competitions_router
from ou25_pipeline.webapp.routers.predictions import router as predictions_router
from ou25_pipeline.webapp.routers.sync import router as sync_router

app = FastAPI(title="OU2.5 Betting Decisions")
app.include_router(predictions_router)
app.include_router(backfill_router)
app.include_router(sync_router)
app.include_router(competitions_router)

# Explicit and overridable rather than derived from __file__: whether
# `parents[N]` from this file's location lands on the repo root depends on
# whether the package was installed editable or as a built wheel, which can
# silently change with the install method (e.g. inside a Docker image).
# FRONTEND_DIST_DIR pins it; the default only needs to hold for local dev,
# where `uv run`/`uvicorn` are always invoked from the repo root anyway.
_FRONTEND_DIST = Path(os.environ.get("FRONTEND_DIST_DIR", "frontend/dist"))
if _FRONTEND_DIST.is_dir():
    # html=True gives SPA fallback: any path Starlette can't match to a
    # built file resolves to index.html, so client-side routing works on a
    # hard refresh/direct link, not just in-app navigation.
    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
# If frontend/dist doesn't exist (backend running standalone, before the
# frontend has been built), "/" simply 404s rather than the app failing to
# start — lets the API be developed and tested on its own.
