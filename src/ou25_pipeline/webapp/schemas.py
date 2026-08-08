"""API response models. Mirrors the columns `service.py` selects plus the
computed `hit` field — kept as a distinct layer from `storage.tables` so the
DB schema and the API contract can evolve independently."""

from datetime import date, datetime

from pydantic import BaseModel


class PredictionOut(BaseModel):
    match_id: str
    rule_version: str
    competition_id: str
    competition_name: str | None
    kickoff_utc: datetime
    home_team: str
    away_team: str
    overdispersion: float | None
    min_prior_matches: int | None
    fav_prob: float | None
    call: str
    decision_zone: str
    bet_side: str | None
    bet_odds: float | None
    edge_estimate: float | None
    skip_reason: str | None
    status: str
    odds_updated_at: datetime | None
    fetch_count: int
    match_status: str | None
    actual_total_goals: int | None
    hit: bool | None


class PredictionsForDateOut(BaseModel):
    date: date
    total_assessed: int
    predictions: list[PredictionOut]


class DatesOut(BaseModel):
    dates: list[date]
