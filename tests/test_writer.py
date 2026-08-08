from sqlalchemy import text

from ou25_pipeline.storage import tables
from ou25_pipeline.storage.writer import upsert


def test_upsert_handles_heterogeneous_keys_within_one_batch(clean_db):
    """The bug found live: multiple bookmakers for one match, each quoting a
    different subset of goal lines, produces match_odds row dicts with
    different key sets in a single upsert() call. Before the fix this
    raised a SQLAlchemy CompileError; every historical (single-bookmaker)
    match_odds upsert had uniform keys and never exposed it."""
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
        conn.execute(text("INSERT INTO seasons (season_id, competition_id, name, year) VALUES ('sn_1','comp_1','24','24')"))
        conn.execute(text("INSERT INTO teams (team_id, name) VALUES ('tm_h','H'),('tm_a','A')"))
        conn.execute(text(
            "INSERT INTO matches (match_id, competition_id, season_id, kickoff_utc, home_team_id, "
            "away_team_id, status) VALUES ('mt_1','comp_1','sn_1','2026-01-01T00:00:00Z','tm_h','tm_a','scheduled')"
        ))

    rows = [
        {"match_id": "mt_1", "bookmaker": "BookA", "goals_2_5_over_close": 1.9, "goals_2_5_under_close": 2.0},
        {  # BookB additionally quotes a line BookA doesn't - the heterogeneous case.
            "match_id": "mt_1", "bookmaker": "BookB",
            "goals_2_5_over_close": 1.85, "goals_2_5_under_close": 2.05,
            "goals_6_5_over_close": 45.0, "goals_6_5_under_close": 1.01,
        },
    ]

    with clean_db.begin() as conn:
        upsert(conn, tables.match_odds, rows)  # must not raise

    with clean_db.connect() as conn:
        result = conn.execute(text(
            "SELECT bookmaker, goals_2_5_over_close, goals_6_5_over_close FROM match_odds "
            "WHERE match_id='mt_1' ORDER BY bookmaker"
        )).mappings().all()

    assert len(result) == 2
    book_a, book_b = result
    assert book_a["bookmaker"] == "BookA"
    assert float(book_a["goals_2_5_over_close"]) == 1.9
    assert book_a["goals_6_5_over_close"] is None  # BookA never quoted this line - stays NULL, not dropped
    assert book_b["bookmaker"] == "BookB"
    assert float(book_b["goals_6_5_over_close"]) == 45.0


def test_upsert_uniform_keys_unaffected(clean_db):
    """The common case (every row shares the same keys) must behave exactly
    as before — no column gets silently added or dropped."""
    with clean_db.begin() as conn:
        upsert(conn, tables.teams, [
            {"team_id": "tm_1", "name": "One", "country": "England"},
            {"team_id": "tm_2", "name": "Two", "country": "Spain"},
        ])

    with clean_db.connect() as conn:
        rows = conn.execute(text("SELECT team_id, name, country FROM teams ORDER BY team_id")).mappings().all()
    assert [dict(r) for r in rows] == [
        {"team_id": "tm_1", "name": "One", "country": "England"},
        {"team_id": "tm_2", "name": "Two", "country": "Spain"},
    ]


def test_upsert_omitted_column_still_uses_table_default(clean_db):
    """A column no row in the batch mentions at all must stay fully absent
    from the statement, not get backfilled with an explicit NULL — or a
    NOT NULL DEFAULT column (predictions.status) would violate its
    constraint instead of picking up the default."""
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO competitions (competition_id, name) VALUES ('comp_1','Test')"))
        conn.execute(text("INSERT INTO seasons (season_id, competition_id, name, year) VALUES ('sn_1','comp_1','24','24')"))
        conn.execute(text("INSERT INTO teams (team_id, name) VALUES ('tm_h','H'),('tm_a','A')"))

    # None of these rows set `status` - must fall through to DEFAULT 'pending', not NULL.
    row = {
        "match_id": "mt_1", "rule_version": "v1", "competition_id": "comp_1", "season_id": "sn_1",
        "kickoff_utc": "2026-01-01T00:00:00Z", "home_team_id": "tm_h", "away_team_id": "tm_a",
        "home_team": "H", "away_team": "A", "call": "NO_BET", "decision_zone": "none",
    }
    with clean_db.begin() as conn:
        upsert(conn, tables.predictions, [row])

    with clean_db.connect() as conn:
        status = conn.execute(text("SELECT status FROM predictions WHERE match_id='mt_1'")).scalar_one()
    assert status == "pending"


def test_upsert_handles_empty_rows(clean_db):
    with clean_db.begin() as conn:
        upsert(conn, tables.teams, [])  # must not raise


def test_upsert_on_conflict_preserves_columns_the_caller_never_mentioned(clean_db):
    """The bug found live: re-syncing the competitions catalog (which only
    ever knows competition_id/name/country/type) silently reset
    is_tracked/tier back to their table defaults for every already-tracked
    competition. Root cause: the ON CONFLICT SET clause was built from every
    non-PK column the *table* has, not the columns the caller actually
    provided - so an omitted column's `excluded.<col>` resolved to its
    default rather than being left out of the UPDATE entirely. This is the
    conflict-update counterpart of test_upsert_omitted_column_still_uses_
    table_default above, which only ever exercised a fresh INSERT."""
    with clean_db.begin() as conn:
        conn.execute(text(
            "INSERT INTO competitions (competition_id, name, is_tracked, tier) VALUES ('comp_1','Old Name', true, 2)"
        ))

    # Mirrors orchestration/catalog.py::sync_competition_catalog exactly -
    # a row that only ever knows the provider's own fields, never is_tracked/tier.
    with clean_db.begin() as conn:
        upsert(conn, tables.competitions, [
            {"competition_id": "comp_1", "name": "New Name", "country": "England", "type": "league"}
        ])

    with clean_db.connect() as conn:
        row = conn.execute(text(
            "SELECT name, is_tracked, tier FROM competitions WHERE competition_id='comp_1'"
        )).mappings().one()
    assert row["name"] == "New Name"  # columns actually provided still update
    assert row["is_tracked"] is True  # columns never provided stay untouched
    assert row["tier"] == 2
