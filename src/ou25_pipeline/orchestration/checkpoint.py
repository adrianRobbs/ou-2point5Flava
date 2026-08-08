from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert

from ou25_pipeline.storage.tables import extraction_state


class Checkpoint:
    """Resume ledger backed by the `extraction_state` Postgres table — lets a
    multi-hour backfill restart after an interruption without re-fetching
    entities already marked done."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def is_done(self, entity_type: str, entity_id: str) -> bool:
        stmt = select(extraction_state.c.status).where(
            extraction_state.c.entity_type == entity_type,
            extraction_state.c.entity_id == entity_id,
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt).first()
        return row is not None and row.status == "done"

    def mark_done(
        self,
        entity_type: str,
        entity_id: str,
        competition_id: str | None = None,
        season_id: str | None = None,
    ) -> None:
        self._upsert_status(entity_type, entity_id, "done", competition_id, season_id)

    def mark_failed(
        self,
        entity_type: str,
        entity_id: str,
        error: str,
        competition_id: str | None = None,
        season_id: str | None = None,
    ) -> None:
        stmt = insert(extraction_state).values(
            entity_type=entity_type,
            entity_id=entity_id,
            competition_id=competition_id,
            season_id=season_id,
            status="failed",
            attempts=1,
            last_error=error[:2000],
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["entity_type", "entity_id"],
            set_={
                "status": "failed",
                "attempts": extraction_state.c.attempts + 1,
                "last_error": error[:2000],
                "competition_id": stmt.excluded.competition_id,
                "season_id": stmt.excluded.season_id,
            },
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def _upsert_status(
        self,
        entity_type: str,
        entity_id: str,
        status: str,
        competition_id: str | None,
        season_id: str | None,
    ) -> None:
        stmt = insert(extraction_state).values(
            entity_type=entity_type,
            entity_id=entity_id,
            competition_id=competition_id,
            season_id=season_id,
            status=status,
            attempts=0,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["entity_type", "entity_id"],
            set_={
                "status": status,
                "competition_id": stmt.excluded.competition_id,
                "season_id": stmt.excluded.season_id,
            },
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)
