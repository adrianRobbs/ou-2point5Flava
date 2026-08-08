from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine

from ou25_pipeline.config import get_settings

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "db" / "schema.sql"


def _psycopg_url(database_url: str) -> str:
    """Neon (and most providers) hand out plain postgresql:// URLs, but we
    standardize on the psycopg (v3) driver rather than psycopg2 — force the
    dialect so `DATABASE_URL` doesn't need a driver-specific scheme."""
    for prefix in ("postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url[len(prefix) :]
    return database_url


@lru_cache
def get_engine() -> Engine:
    return create_engine(_psycopg_url(get_settings().database_url), pool_pre_ping=True)


def bootstrap_schema(engine: Engine | None = None) -> None:
    """Apply db/schema.sql. Safe to re-run — every statement is idempotent
    (CREATE TABLE/INDEX IF NOT EXISTS, CREATE OR REPLACE VIEW).

    Executed via the raw DBAPI connection rather than SQLAlchemy's `text()`:
    the schema uses Postgres `::type` casts, which `text()` would otherwise
    misparse as `:bindparam` placeholders, and it's one multi-statement
    script rather than something worth splitting on ';'.
    """
    engine = engine or get_engine()
    sql = SCHEMA_PATH.read_text()
    raw_conn = engine.raw_connection()
    try:
        with raw_conn.cursor() as cursor:
            cursor.execute(sql)
        raw_conn.commit()
    finally:
        raw_conn.close()
