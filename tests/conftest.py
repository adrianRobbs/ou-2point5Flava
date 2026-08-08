import os
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, text

from ou25_pipeline.storage.db import _psycopg_url, bootstrap_schema

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://postgres:test@localhost:15432/ou25_test"
)


def _target(url: str) -> tuple[str, str]:
    """(host, database name) — enough to tell two connection strings apart
    without ever handling/logging the embedded credentials."""
    parts = urlsplit(url)
    return (parts.hostname or "", parts.path.lstrip("/"))


# Guard rail: this suite TRUNCATEs every table via `clean_db`. The real
# danger isn't "remote database" (a dedicated test project, e.g. on Neon, is
# fine) — it's "same database as production". Pointing TEST_DATABASE_URL at
# the real Neon DATABASE_URL once already wiped a fully-backfilled dataset,
# so: always allow localhost, otherwise require a DATABASE_URL to compare
# against and require it to name a *different* (host, database).
_test_target = _target(TEST_DATABASE_URL)
_is_local = _test_target[0] in ("localhost", "127.0.0.1")

if not _is_local:
    _prod_url = os.environ.get("DATABASE_URL", "")
    _prod_target = _target(_prod_url) if _prod_url else None
    if _prod_target is None or _test_target == _prod_target:
        # Never interpolate the raw URLs here — they carry embedded credentials.
        raise RuntimeError(
            f"TEST_DATABASE_URL targets {_test_target!r}, which is not localhost "
            f"and either matches DATABASE_URL or DATABASE_URL isn't set to compare "
            f"against. Refusing to run — this suite truncates every table."
        )

_TABLES_TO_CLEAR = [
    "extraction_state",
    "predictions",
    "match_events",
    "match_lineup_players",
    "match_lineups",
    "match_odds",
    "match_team_stats",
    "team_injuries_suspensions",
    "matches",
    "standings",
    "referees",
    "teams",
    "seasons",
    "competitions",
]


@pytest.fixture(scope="session")
def db_engine():
    engine = create_engine(_psycopg_url(TEST_DATABASE_URL))
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"No test Postgres reachable at {TEST_DATABASE_URL}: {exc}")
    bootstrap_schema(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def clean_db(db_engine):
    with db_engine.begin() as conn:
        conn.execute(text("TRUNCATE " + ", ".join(_TABLES_TO_CLEAR) + " CASCADE"))
    return db_engine
