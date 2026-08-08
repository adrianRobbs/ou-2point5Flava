"""Thin, direct wrapper over the two Render API calls the backfill-management
page needs. Deliberately not built on `api.client.StatsAPIClient` — that
class's retry/backoff/rate-limit/quota machinery exists specifically for
TheStatsAPI's behavior (ordinary 429 vs monthly quota, etc.) and doesn't
transfer to a different external service with its own semantics; two direct
`httpx` calls are simpler and more honest than force-fitting a client built
for something else.
"""

import httpx

_RENDER_API_BASE = "https://api.render.com/v1"


class RenderNotConfiguredError(Exception):
    """RENDER_API_KEY / RENDER_SERVICE_ID aren't set. Raised, not silently
    swallowed, so the route layer can turn it into an explicit 501 rather
    than a confusing downstream failure."""


def _require(api_key: str | None, service_id: str | None) -> tuple[str, str]:
    if not api_key or not service_id:
        raise RenderNotConfiguredError(
            "RENDER_API_KEY and RENDER_SERVICE_ID must both be set to trigger a backfill job."
        )
    return api_key, service_id


def trigger_job(api_key: str | None, service_id: str | None, start_command: str) -> dict:
    """POSTs a one-off Render Job with the given start command. The command
    is always just `ou25-pipeline backfill --competition ... --season ...`
    — the exact thing a human would type — assembled by the caller
    (`routers/backfill.py::trigger_sync`), never anything this module
    constructs itself.
    """
    key, sid = _require(api_key, service_id)
    response = httpx.post(
        f"{_RENDER_API_BASE}/services/{sid}/jobs",
        headers={"Authorization": f"Bearer {key}"},
        json={"startCommand": start_command},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def get_job_status(api_key: str | None, job_id: str) -> dict:
    if not api_key:
        raise RenderNotConfiguredError("RENDER_API_KEY must be set to check job status.")
    response = httpx.get(
        f"{_RENDER_API_BASE}/jobs/{job_id}",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15.0,
    )
    response.raise_for_status()
    return response.json()
