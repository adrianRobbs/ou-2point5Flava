"""Registers a competition/season/team on demand, for callers that only have
an id from a *live* API response and cannot assume `backfill` has ever run
for that league — `predictions` and `matches` both carry the same foreign
keys `backfill` has always satisfied by resolving and upserting before
writing a match row, and a live-discovered fixture is not exempt from that.

Mirrors `pipeline.py`'s `resolve_competition`/`resolve_season`, minus the
free-text matching those need — a live match already carries an exact id,
so this only ever needs a direct lookup, not a search.
"""

from sqlalchemy import Engine, text

from ou25_pipeline.api import endpoints
from ou25_pipeline.api.client import StatsAPIClient
from ou25_pipeline.orchestration import mapping
from ou25_pipeline.storage import tables
from ou25_pipeline.storage.writer import upsert


def ensure_competition_registered(engine: Engine, client: StatsAPIClient, competition_id: str) -> bool:
    """No-ops (one cheap SELECT, no API call) if already known. Returns
    whether a new row was actually written."""
    with engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM competitions WHERE competition_id = :id"), {"id": competition_id}
        ).first()
    if exists:
        return False
    competition = endpoints.get_competition(client, competition_id)
    if competition is None:
        return False
    with engine.begin() as conn:
        upsert(conn, tables.competitions, [mapping.map_competition_row(competition)])
    return True


def ensure_season_registered(engine: Engine, client: StatsAPIClient, competition_id: str, season_id: str) -> bool:
    with engine.connect() as conn:
        exists = conn.execute(text("SELECT 1 FROM seasons WHERE season_id = :id"), {"id": season_id}).first()
    if exists:
        return False
    seasons = endpoints.list_seasons(client, competition_id)
    season = next((s for s in seasons if s.id == season_id), None)
    if season is None:
        return False
    with engine.begin() as conn:
        upsert(conn, tables.seasons, [mapping.map_season_row(competition_id, season)])
    return True


def ensure_teams_registered(
    engine: Engine, client: StatsAPIClient, team_ids: list[str], team_names: dict[str, str]
) -> None:
    """`team_names` comes from the match payload itself (always present,
    unlike the `/teams/{id}` detail lookup, which can 404) so a team still
    gets registered with a real name even when its detail fetch fails —
    only `country` falls back to unknown in that case, matching
    `map_team_row`'s own existing fallback.
    """
    if not team_ids:
        return
    with engine.connect() as conn:
        known = {
            row[0] for row in conn.execute(
                text("SELECT team_id FROM teams WHERE team_id = ANY(:ids)"), {"ids": team_ids}
            )
        }
    missing = [t for t in team_ids if t not in known]
    if not missing:
        return
    rows = []
    for team_id in missing:
        detail = endpoints.get_team(client, team_id)
        name = team_names.get(team_id) or (detail.name if detail else team_id)
        rows.append(mapping.map_team_row(team_id, name, detail))
    with engine.begin() as conn:
        upsert(conn, tables.teams, rows)


def ensure_fixture_registered(
    engine: Engine,
    client: StatsAPIClient,
    competition_id: str,
    season_id: str,
    home_team_id: str,
    away_team_id: str,
    home_team_name: str,
    away_team_name: str,
) -> None:
    """The one call live callers actually need: competition, then season,
    then both teams — in FK-dependency order — for a single fixture."""
    ensure_competition_registered(engine, client, competition_id)
    ensure_season_registered(engine, client, competition_id, season_id)
    ensure_teams_registered(
        engine, client, [home_team_id, away_team_id],
        {home_team_id: home_team_name, away_team_id: away_team_name},
    )
