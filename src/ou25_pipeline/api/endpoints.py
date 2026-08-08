"""Typed wrappers over each TheStatsAPI endpoint group. Each function parses
the response into the pydantic models from `models.schemas`, returning None
(rather than raising) for satellite endpoints where a 404 means "not yet
available" (unannounced lineups, no xG coverage, etc.) rather than an error.
"""

from ou25_pipeline.api.client import NotFoundError, StatsAPIClient
from ou25_pipeline.models.schemas import (
    Competition,
    Lineup,
    Match,
    MatchDetail,
    MatchOdds,
    MatchStats,
    Referee,
    Season,
    Standing,
    Team,
    TeamInjuriesSuspensions,
    Timeline,
)


def _get_or_none(client: StatsAPIClient, path: str) -> dict | None:
    try:
        return client.get(path)
    except NotFoundError:
        return None


def list_competitions(client: StatsAPIClient, **filters: object) -> list[Competition]:
    rows = client.get_paginated("/football/competitions", params=filters)
    return [Competition.model_validate(r) for r in rows]


def list_seasons(client: StatsAPIClient, competition_id: str) -> list[Season]:
    payload = client.get(f"/football/competitions/{competition_id}/seasons")
    data = (payload or {}).get("data") or []
    return [Season.model_validate(r) for r in data]


def get_standings(
    client: StatsAPIClient, competition_id: str, season_id: str, group: str | None = None
) -> list[Standing]:
    params = {"group": group} if group else None
    payload = client.get(
        f"/football/competitions/{competition_id}/seasons/{season_id}/standings", params=params
    )
    data = (payload or {}).get("data") or []
    return [Standing.model_validate(r) for r in data]


def list_teams(client: StatsAPIClient, **filters: object) -> list[Team]:
    rows = client.get_paginated("/football/teams", params=filters)
    return [Team.model_validate(r) for r in rows]


def get_team(client: StatsAPIClient, team_id: str) -> Team | None:
    payload = _get_or_none(client, f"/football/teams/{team_id}")
    data = (payload or {}).get("data") if payload else None
    return Team.model_validate(data) if data else None


def get_competition(client: StatsAPIClient, competition_id: str) -> Competition | None:
    payload = _get_or_none(client, f"/football/competitions/{competition_id}")
    data = (payload or {}).get("data") if payload else None
    return Competition.model_validate(data) if data else None


def list_matches(client: StatsAPIClient, **filters: object) -> list[Match]:
    rows = client.get_paginated("/football/matches", params=filters)
    return [Match.model_validate(r) for r in rows]


def get_match_detail(client: StatsAPIClient, match_id: str) -> MatchDetail | None:
    payload = _get_or_none(client, f"/football/matches/{match_id}")
    data = (payload or {}).get("data") if payload else None
    return MatchDetail.model_validate(data) if data else None


def get_match_referee(client: StatsAPIClient, match_id: str) -> Referee | None:
    payload = _get_or_none(client, f"/football/matches/{match_id}/referee")
    data = (payload or {}).get("data") if payload else None
    referee = (data or {}).get("referee") if data else None
    return Referee.model_validate(referee) if referee else None


def get_match_stats(client: StatsAPIClient, match_id: str) -> MatchStats | None:
    payload = _get_or_none(client, f"/football/matches/{match_id}/stats")
    data = (payload or {}).get("data") if payload else None
    return MatchStats.model_validate(data) if data else None


def get_match_odds(client: StatsAPIClient, match_id: str) -> MatchOdds | None:
    payload = _get_or_none(client, f"/football/matches/{match_id}/odds")
    data = (payload or {}).get("data") if payload else None
    return MatchOdds.model_validate(data) if data else None


def get_match_lineups(client: StatsAPIClient, match_id: str) -> Lineup | None:
    payload = _get_or_none(client, f"/football/matches/{match_id}/lineups")
    data = (payload or {}).get("data") if payload else None
    return Lineup.model_validate(data) if data else None


def get_match_timeline(client: StatsAPIClient, match_id: str) -> Timeline | None:
    payload = _get_or_none(client, f"/football/matches/{match_id}/timeline")
    data = (payload or {}).get("data") if payload else None
    return Timeline.model_validate(data) if data else None


def get_match_shotmap(client: StatsAPIClient, match_id: str) -> list[dict] | None:
    payload = _get_or_none(client, f"/football/matches/{match_id}/shotmap")
    return (payload or {}).get("data") if payload else None


def get_team_injuries_suspensions(client: StatsAPIClient, team_id: str) -> TeamInjuriesSuspensions:
    payload = client.get(f"/football/teams/{team_id}/injuries-suspensions")
    data = (payload or {}).get("data") or {}
    return TeamInjuriesSuspensions.model_validate(data)
