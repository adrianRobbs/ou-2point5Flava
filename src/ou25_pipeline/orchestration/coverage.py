"""What competitions/seasons we know about, and how our own row counts
compare to what the live API reports. Used by both the webapp's Backfill
page (`webapp/routers/backfill.py`) and the `sync-backfill` CLI command —
lives in `orchestration/`, not `webapp/`, because neither user of it is
web-specific; this is the same shared-logic-over-two-callers shape as
`orchestration/daily_sync.py`.

Two-tier by design (see `market/persistence.py`'s docstring for the same
split applied to a different concern): listing seasons costs one live call
merged with a DB-only count; checking one specific season's completeness
against the live total is a second, separate call, kept on-demand rather
than eagerly diffing every season of every competition on every page load
or CLI run.
"""

from dataclasses import dataclass

from sqlalchemy import Engine, text

from ou25_pipeline.api import endpoints
from ou25_pipeline.api.client import StatsAPIClient


def list_tracked_competitions(engine: Engine) -> list[dict]:
    """Every competition explicitly flagged `is_tracked` (see
    `orchestration/catalog.py`) — deliberately *not* every competition that
    happens to be registered in our DB. A competition can land in
    `competitions` without ever being deliberately tracked (e.g.
    auto-registered via live discovery — see `orchestration/registration.py`,
    the Scottish Premiership case); it shouldn't silently show up on the
    Backfill page just because a fixture from it was seen once. `is_tracked`
    requires the row to already exist (`catalog.set_competition_tracked`),
    so `is_registered` is always true here — kept in the response shape for
    API stability rather than dropped, since the frontend already reads it.
    """
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT competition_id, name, tier FROM competitions WHERE is_tracked ORDER BY competition_id"
        )).mappings().all()
    return [
        {"competition_id": r["competition_id"], "name": r["name"], "tier": r["tier"], "is_registered": True}
        for r in rows
    ]


def list_seasons_with_coverage(client: StatsAPIClient, engine: Engine, competition_id: str) -> list[dict]:
    """Every season the provider reports for this competition, each paired
    with our own match count (0 if we have never touched it at all)."""
    seasons = endpoints.list_seasons(client, competition_id)

    with engine.connect() as conn:
        our_counts = {
            row.season_id: row.n
            for row in conn.execute(
                text("SELECT season_id, COUNT(*) AS n FROM matches WHERE competition_id = :c GROUP BY season_id"),
                {"c": competition_id},
            )
        }

    return [
        {
            "season_id": season.id,
            "name": season.name,
            "year": season.year,
            "is_current": season.is_current,
            "our_match_count": our_counts.get(season.id, 0),
        }
        for season in seasons
    ]


def check_season_coverage(
    client: StatsAPIClient, engine: Engine, competition_id: str, season_id: str
) -> dict:
    """The on-demand, slower check: how many matches the live API actually
    reports for this season versus how many we have. `expected_total`
    counts every match the provider lists (finished or not) since a season
    in progress is never going to be "complete" by a finished-only count —
    `status` reflects that distinction instead.

    `earliest_kickoff`/`latest_kickoff` are derived from that same match
    list, not a separate call — the provider has no real season start/end
    date at all (see `models/schemas.py::Season`'s docstring, only a display
    name and year codes), so this is the only source of an actual calendar
    range. For a season whose fixture list isn't fully published yet (e.g. a
    newly-started one), `latest_kickoff` reflects only what's been announced
    so far, not necessarily the season's real end.
    """
    live_matches = endpoints.list_matches(client, competition_id=competition_id, season_id=season_id)
    expected_total = len(live_matches)
    kickoffs = [m.utc_date for m in live_matches]

    with engine.connect() as conn:
        our_count = conn.execute(
            text("SELECT COUNT(*) FROM matches WHERE competition_id = :c AND season_id = :s"),
            {"c": competition_id, "s": season_id},
        ).scalar_one()

    if our_count == 0:
        status = "not_started"
    elif our_count >= expected_total:
        status = "complete"
    else:
        status = "partial"

    return {
        "competition_id": competition_id,
        "season_id": season_id,
        "expected_total": expected_total,
        "our_count": our_count,
        "status": status,
        "earliest_kickoff": min(kickoffs) if kickoffs else None,
        "latest_kickoff": max(kickoffs) if kickoffs else None,
    }


@dataclass(frozen=True)
class CoverageGap:
    competition_id: str
    season_id: str
    year: str
    our_count: int
    expected_finished: int
    status: str  # 'partial' | 'not_started' — never 'complete', that's not a gap


def find_coverage_gaps(client: StatsAPIClient, engine: Engine, competition_ids: list[str]) -> list[CoverageGap]:
    """Checks live coverage for every season *already registered in our DB*
    for each competition — not every season the provider has ever run (that
    would multiply the live-call count several-fold for no benefit, since a
    season we've never touched at all isn't something `sync-backfill` should
    decide on its own is worth backfilling; add it once via `backfill`
    first, the same as any other new season, and it becomes a candidate
    gap on the next run).

    Deliberately *not* built on `check_season_coverage` — that function's
    `expected_total` counts every match the provider lists, played or not
    (right for the webapp's season-progress display, which wants to show
    "26 of 55 played" even mid-season). `run_backfill` only ever fetches
    `status="finished"` matches, so a gap here has to mean the same thing:
    counted live-discovered against a season with zero matches played yet
    (e.g. a brand new season only ever touched by `discover-fixtures`)
    would report a large "gap" that `sync-backfill` then correctly does
    nothing about — technically not wrong, but confusing enough in practice
    to be worth avoiding rather than living with.
    """
    if not competition_ids:
        return []

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT season_id, competition_id, year FROM seasons WHERE competition_id = ANY(:ids)"),
            {"ids": competition_ids},
        ).mappings().all()

    gaps = []
    for row in rows:
        competition_id, season_id = row["competition_id"], row["season_id"]
        finished_matches = endpoints.list_matches(
            client, competition_id=competition_id, season_id=season_id, status="finished"
        )
        expected_finished = len(finished_matches)

        with engine.connect() as conn:
            our_count = conn.execute(
                text("SELECT COUNT(*) FROM matches WHERE competition_id = :c AND season_id = :s"),
                {"c": competition_id, "s": season_id},
            ).scalar_one()

        if our_count < expected_finished:
            gaps.append(CoverageGap(
                competition_id=competition_id,
                season_id=season_id,
                year=row["year"],
                our_count=our_count,
                expected_finished=expected_finished,
                status="not_started" if our_count == 0 else "partial",
            ))
    return gaps
