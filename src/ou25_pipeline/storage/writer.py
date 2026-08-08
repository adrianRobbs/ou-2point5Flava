from typing import Any

from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection


def upsert(conn: Connection, table: Table, rows: list[dict[str, Any]]) -> None:
    """INSERT ... ON CONFLICT (primary key) DO UPDATE for every non-PK column.
    A no-op if `rows` is empty — callers don't need to guard against that.
    """
    if not rows:
        return

    # Every row in one multi-row INSERT must carry the same set of keys, or
    # SQLAlchemy's VALUES compilation raises CompileError ("... is
    # explicitly rendered as a boundparameter ..."). Found live: a scheduled
    # match with multiple bookmakers, each quoting a different subset of
    # goal lines, produced match_odds row dicts with different key sets in
    # one batch — single-bookmaker historical data never had non-uniform
    # rows to expose this. Filled to the union of keys *actually present in
    # this batch*, not the full table schema — a column no row in the batch
    # sets at all stays fully absent from the statement, so a table default
    # (e.g. predictions.status's DEFAULT 'pending') still applies rather
    # than being overwritten with an explicit NULL.
    if len({frozenset(row) for row in rows}) > 1:
        all_keys = {key for row in rows for key in row}
        rows = [{key: row.get(key) for key in all_keys} for row in rows]

    # On conflict, only touch columns the caller actually provided — not
    # every non-PK column the table happens to have. Found live: `upsert()`
    # was building the SET clause from the *full table schema*, so any
    # caller that (correctly, by this function's own contract above) omits
    # a column it doesn't know about ends up resetting that column to its
    # default on every conflict, silently clobbering real state. Concretely:
    # re-syncing the competitions catalog (which only ever knows
    # competition_id/name/country/type) reset `is_tracked`/`tier` back to
    # false for every already-tracked competition on each sync, and
    # `run_backfill` re-upserting a competition/season row on every run hit
    # the exact same bug. `rows[0]` is safe to key off here — the
    # normalization above already made every row's key set identical.
    pk_cols = {c.name for c in table.primary_key.columns}
    provided_cols = set(rows[0])
    update_cols = {c.name: c for c in table.columns if c.name not in pk_cols and c.name in provided_cols}

    stmt = insert(table).values(rows)
    if update_cols:
        stmt = stmt.on_conflict_do_update(
            index_elements=list(pk_cols),
            set_={name: stmt.excluded[name] for name in update_cols},
        )
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=list(pk_cols))

    conn.execute(stmt)
