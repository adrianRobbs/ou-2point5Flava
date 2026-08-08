"""Write path for the `predictions` table — the persisted output of the
live decision engine, read by the webapp.

Two phases, matching the module's two entry points:

- `discover_fixtures`: as soon as a fixture is known (likely before its odds
  market is mature), insert a placeholder row so the frontend can show
  "coming up, awaiting update" instead of nothing at all.
- `upsert_refreshed`: once a real classification exists, overwrite the
  placeholder (or a prior classification) with it and flip status to
  'updated'.

Kept separate from `market/live.py` (which only reads — the live API and the
finished-matches table) so the read/compute path and the write path stay
independently testable, matching the existing
ladder.py/decision.py/backtest.py/live.py split where each module owns one
concern.
"""

from sqlalchemy import Engine, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from ou25_pipeline.market.decision import NO_BET, RULE_VERSION
from ou25_pipeline.market.live import Recommendation, UpcomingMatch
from ou25_pipeline.storage.tables import predictions

_UPSERT_COLUMNS = (
    "competition_id", "season_id", "kickoff_utc", "home_team_id", "away_team_id",
    "home_team", "away_team", "overdispersion", "min_prior_matches",
    "fav_prob", "call", "decision_zone", "bet_side", "bet_odds",
    "edge_estimate", "skip_reason",
)


def _clean(value: float | None) -> float | None:
    """NaN -> None. Postgres float8 can store NaN, but JSON — and therefore
    the API this table feeds — cannot, so it is normalised at the write
    boundary rather than pushed downstream to every reader."""
    if value is None:
        return None
    return None if value != value else value  # NaN is the only value != itself


def discover_fixtures(engine: Engine, upcoming_matches: list[UpcomingMatch]) -> int:
    """Insert a 'pending' placeholder for each fixture not already known.

    `on_conflict_do_nothing` is deliberate, not just the simple default: a
    discover pass runs independently of and more often than a refresh pass
    sees new fixtures, so it must never be able to clobber a row a refresh
    pass already filled in with a real classification.

    Callers must have already registered each match's competition, season,
    and both teams (`orchestration.registration.ensure_fixture_registered`)
    — `predictions` carries the same foreign keys `matches` does, and a
    live-discovered fixture is not exempt from them just because
    `backfill` may never have run for its league.
    """
    if not upcoming_matches:
        return 0
    rows = [{
        "match_id": m.match_id,
        "rule_version": RULE_VERSION,
        "competition_id": m.competition_id,
        "season_id": m.season_id,
        "kickoff_utc": m.kickoff_utc,
        "home_team_id": m.home_team_id,
        "away_team_id": m.away_team_id,
        "home_team": m.home_team,
        "away_team": m.away_team,
        "call": NO_BET,
        "decision_zone": "none",
        "skip_reason": "not_yet_priced",
        "status": "pending",
    } for m in upcoming_matches]

    # `RETURNING` rather than `result.rowcount`: a multi-row INSERT ... ON
    # CONFLICT DO NOTHING reports an unreliable rowcount through psycopg/
    # SQLAlchemy (observed -1 against real Postgres even when every row
    # inserted correctly). RETURNING only yields rows actually written —
    # a skipped conflict never appears in it — so counting those rows is
    # exact regardless of driver rowcount quirks.
    stmt = pg_insert(predictions).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=["match_id", "rule_version"])
    stmt = stmt.returning(predictions.c.match_id)
    with engine.begin() as conn:
        result = conn.execute(stmt)
        return len(result.fetchall())


def upsert_refreshed(engine: Engine, recommendations: list[Recommendation]) -> int:
    """Write a real classification, flipping status to 'updated'.

    `fetch_count` increments off the table's own current value rather than
    a value carried in the incoming row (there isn't one to carry), and
    `first_fetched_at` is left out of the SET clause entirely so the
    original discover timestamp survives every subsequent refresh.

    Same registration precondition as `discover_fixtures` — a match sourced
    straight from the live API (rather than read back from `predictions`,
    see `matches_needing_refresh`) may not have a registered competition/
    season/teams yet.
    """
    if not recommendations:
        return 0
    rows = [{
        "match_id": r.match.match_id,
        "rule_version": r.decision.rule_version,
        "competition_id": r.match.competition_id,
        "season_id": r.match.season_id,
        "kickoff_utc": r.match.kickoff_utc,
        "home_team_id": r.match.home_team_id,
        "away_team_id": r.match.away_team_id,
        "home_team": r.match.home_team,
        "away_team": r.match.away_team,
        "overdispersion": _clean(r.overdispersion),
        "min_prior_matches": r.min_prior_matches,
        "fav_prob": _clean(r.fav_prob),
        "call": r.decision.call,
        "decision_zone": r.decision.zone,
        "bet_side": r.decision.side or None,
        "bet_odds": _clean(r.decision.odds),
        "edge_estimate": r.decision.edge_estimate,
        "skip_reason": r.decision.skip_reason or None,
    } for r in recommendations]

    stmt = pg_insert(predictions).values(rows)
    update_cols = {name: stmt.excluded[name] for name in _UPSERT_COLUMNS}
    update_cols["status"] = "updated"
    update_cols["odds_updated_at"] = text("now()")
    update_cols["fetch_count"] = predictions.c.fetch_count + 1
    stmt = stmt.on_conflict_do_update(index_elements=["match_id", "rule_version"], set_=update_cols)
    stmt = stmt.returning(predictions.c.match_id)  # see discover_fixtures for why not rowcount
    with engine.begin() as conn:
        result = conn.execute(stmt)
        return len(result.fetchall())


def matches_needing_refresh(engine: Engine, competition_ids: list[str]) -> list[UpcomingMatch]:
    """Rows in `predictions` whose odds should be (re)fetched: freshly
    discovered placeholders, or previously classified matches that haven't
    kicked off yet — their price may have moved since the last refresh.

    Reads fixture metadata (team ids/names, kickoff) straight from this
    table rather than re-querying the live API for it, since `discover
    fixtures` already captured everything needed to rebuild an
    `UpcomingMatch`.
    """
    if not competition_ids:
        return []
    query = text("""
        SELECT match_id, competition_id, season_id, kickoff_utc, home_team_id, away_team_id,
               home_team, away_team
        FROM predictions
        WHERE competition_id = ANY(:competition_ids)
          AND rule_version = :rule_version
          AND (status = 'pending' OR kickoff_utc > now())
        ORDER BY kickoff_utc
    """)
    with engine.connect() as conn:
        rows = conn.execute(
            query, {"competition_ids": competition_ids, "rule_version": RULE_VERSION}
        ).mappings().all()
    return [UpcomingMatch(
        match_id=r["match_id"], competition_id=r["competition_id"], season_id=r["season_id"],
        kickoff_utc=r["kickoff_utc"], home_team_id=r["home_team_id"], away_team_id=r["away_team_id"],
        home_team=r["home_team"], away_team=r["away_team"],
    ) for r in rows]


def matches_awaiting_result(engine: Engine, competition_ids: list[str]) -> list[str]:
    """match_ids in `predictions` whose kickoff has passed but `matches` has
    no finished result for them yet — the set `refresh-odds` should call
    `orchestration.results.sync_finished_result` for. No live/in-play
    signal is involved: "kickoff has passed" is a sufficient trigger since
    this project only ever bets pre-match, and a match still in progress
    just fails the finished check and gets tried again next refresh.
    """
    if not competition_ids:
        return []
    query = text("""
        SELECT DISTINCT p.match_id
        FROM predictions p
        LEFT JOIN matches m ON m.match_id = p.match_id
        WHERE p.competition_id = ANY(:competition_ids)
          AND p.kickoff_utc <= now()
          AND (m.match_id IS NULL OR m.status <> 'finished')
    """)
    with engine.connect() as conn:
        return [row[0] for row in conn.execute(query, {"competition_ids": competition_ids})]
