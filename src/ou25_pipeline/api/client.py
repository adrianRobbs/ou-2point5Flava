import logging
import threading
import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

logger = logging.getLogger(__name__)


class NotFoundError(Exception):
    """404 from a satellite endpoint — expected-missing data (unannounced
    lineups, no heatmap coverage, etc.), not a failure. Never retried."""


class RetryableAPIError(Exception):
    """429 (ordinary rate limit) or 5xx — safe to retry with backoff."""


class QuotaExceededError(Exception):
    """A 429 whose body identifies it as a hard quota cap (e.g.
    USAGE_LIMIT_EXCEEDED), not an ordinary rate limit. Retrying does
    nothing — every subsequent request hits the same wall until the quota
    resets or the plan is upgraded — so this is never retried and the
    caller should stop issuing requests entirely, not just skip one match."""


def _extract_error_code(response: httpx.Response) -> str | None:
    try:
        return response.json().get("error", {}).get("code")
    except (ValueError, AttributeError):
        return None


class RateLimiter:
    """Blocking token bucket. Rate-limit headers aren't confirmed in the
    provider's public docs, so this enforces the configured plan limit
    proactively rather than reacting to server-side signals alone."""

    def __init__(self, requests_per_minute: int) -> None:
        self._capacity = requests_per_minute
        self._tokens = float(requests_per_minute)
        self._refill_rate = requests_per_minute / 60.0
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_rate)
                self._last_refill = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait_time = (1 - self._tokens) / self._refill_rate
            time.sleep(wait_time)


class StatsAPIClient:
    def __init__(self, base_url: str, api_key: str, requests_per_minute: int) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )
        self._rate_limiter = RateLimiter(requests_per_minute)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "StatsAPIClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @retry(
        retry=retry_if_exception_type(RetryableAPIError),
        wait=wait_exponential_jitter(initial=1, max=60),
        stop=stop_after_attempt(6),
        reraise=True,
    )
    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        self._rate_limiter.acquire()
        response = self._http.get(path, params=params)

        if response.status_code == 404:
            raise NotFoundError(path)

        if response.status_code == 429:
            error_code = _extract_error_code(response)
            if error_code == "USAGE_LIMIT_EXCEEDED":
                raise QuotaExceededError(f"Monthly usage limit exceeded on {path}")
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                time.sleep(float(retry_after))
            raise RetryableAPIError(f"429 rate limited on {path}")

        if response.status_code >= 500:
            raise RetryableAPIError(f"{response.status_code} on {path}")

        if response.status_code == 409:
            # Live-endpoint conflict (fetching live data for a non-live match).
            # Not applicable to historical backfill; treat as no data.
            return None

        response.raise_for_status()
        return response.json()

    def get_paginated(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Walks `page`/`per_page` until `meta.total_pages` is exhausted."""
        results: list[dict[str, Any]] = []
        page = 1
        params = dict(params or {})
        params.setdefault("per_page", 100)

        while True:
            params["page"] = page
            payload = self.get(path, params=params)
            if payload is None:
                break

            data = payload.get("data") or []
            results.extend(data)

            meta = payload.get("meta") or {}
            total_pages = meta.get("total_pages", 1)
            if page >= total_pages:
                break
            page += 1

        return results
