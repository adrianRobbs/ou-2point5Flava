"""The full competitions catalog and which ones we operationally track.

`is_tracked` is the sole source of truth for operational scope — what
`discover-fixtures`/`refresh-odds`/`sync-backfill` and the webapp's manual
sync buttons act on (see `list_tracked_competition_ids`). This is
deliberately separate from `export/match_csv.py`'s `TIER_BY_COMPETITION`,
which is a frozen backtest classification for the 13 already-studied
competitions and has no meaning for anything newly tracked here — a
competition added through this module has never been backtested, so it has
no tier until someone studies it, not a guessed one.
"""

from dataclasses import asdict, dataclass

from sqlalchemy import Engine, text

from ou25_pipeline.api import endpoints
from ou25_pipeline.api.client import StatsAPIClient
from ou25_pipeline.orchestration.mapping import map_competition_row
from ou25_pipeline.storage import tables
from ou25_pipeline.storage.writer import upsert


def sync_competition_catalog(client: StatsAPIClient, engine: Engine) -> int:
    """Fetches every competition the provider knows about (confirmed live:
    150 total, 2 pages — cheap enough to always fetch in full rather than
    filter) and upserts it into `competitions`.

    Rows here never carry `is_tracked`/`tier` — `upsert()` only touches
    columns present in the incoming row dict (see `storage/writer.py`), so
    re-running this can never clobber an existing tracking decision. Safe
    to call as often as wanted.
    """
    competitions = endpoints.list_competitions(client)
    rows = [map_competition_row(c) for c in competitions]
    with engine.begin() as conn:
        upsert(conn, tables.competitions, rows)
    return len(rows)


@dataclass(frozen=True)
class CatalogEntry:
    competition_id: str
    name: str
    country: str | None
    type: str | None
    is_tracked: bool
    tier: int | None


def list_all_competitions(engine: Engine) -> list[dict]:
    """Every competition we know about (post `sync_competition_catalog`),
    tracked or not — the new Competitions page splits/groups this list
    client-side rather than the backend returning two pre-split shapes."""
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT competition_id, name, country, type, is_tracked, tier "
            "FROM competitions ORDER BY is_tracked DESC, tier NULLS LAST, name"
        )).mappings().all()
    return [asdict(CatalogEntry(**row)) for row in rows]


def list_tracked_competition_ids(engine: Engine) -> list[str]:
    """The operational scope: every competition explicitly flagged
    `is_tracked`. Replaces `TIER_BY_COMPETITION.keys()` at every call site
    that decides what to discover/refresh/backfill — see this module's
    docstring for why tier itself isn't that signal."""
    with engine.connect() as conn:
        return [
            row[0] for row in conn.execute(
                text("SELECT competition_id FROM competitions WHERE is_tracked ORDER BY competition_id")
            )
        ]


def set_competition_tracked(engine: Engine, competition_id: str, tracked: bool, tier: int | None = None) -> None:
    """Requires the competition to already be in `competitions` (i.e.
    `sync_competition_catalog` has run, or `backfill`/live discovery
    registered it some other way) — this only ever flips the flag on an
    existing row, it does not register a new competition on its own."""
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE competitions SET is_tracked = :tracked, tier = :tier "
                "WHERE competition_id = :id RETURNING competition_id"
            ),
            {"tracked": tracked, "tier": tier, "id": competition_id},
        )
        if result.first() is None:
            raise ValueError(f"Competition {competition_id!r} not found — run the catalog sync first.")
