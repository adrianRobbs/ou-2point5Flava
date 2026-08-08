"""Pydantic models mirroring TheStatsAPI JSON shapes.

These exist to validate/normalize API responses *before* they're mapped to
DB rows — a schema drift on the provider's side fails loudly here rather
than silently writing bad data. `extra="ignore"` throughout: we only model
fields this pipeline actually uses, not the full response shape.

Verified 2026-07-24 against live responses for a real Premier League match
(mt_745359007) — several fields here differ from the Phase 1 doc-based
guesses (seasons have no start_date/end_date; /stats and /odds are keyed by
stat/market name rather than home/away; lineups use `starting_xi`; timeline
events nest team/player as refs, not ids).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Competition(ApiModel):
    id: str
    name: str
    country: str | None = None
    type: str | None = None


class Season(ApiModel):
    id: str
    name: str
    year: str
    start_year: int | None = None
    end_year: int | None = None
    is_current: bool = False


class TeamRef(ApiModel):
    id: str
    name: str


class Team(ApiModel):
    id: str
    name: str
    country: str | None = None


class Standing(ApiModel):
    team: TeamRef
    position: int | None = None
    matches_played: int | None = None
    wins: int | None = None
    draws: int | None = None
    losses: int | None = None
    goals_for: int | None = None
    goals_against: int | None = None
    goal_difference: int | None = None
    points: int | None = None
    group_label: str | None = None


class RefereeCareer(ApiModel):
    games: int | None = None
    yellow_cards: int | None = None
    red_cards: int | None = None
    yellow_red_cards: int | None = None


class Referee(ApiModel):
    id: str
    name: str
    country: str | None = None
    career: RefereeCareer | None = None


class Venue(ApiModel):
    name: str | None = None
    city: str | None = None


class Score(ApiModel):
    home: int | None = None
    away: int | None = None
    # Only present on the single-match detail endpoint, not the matches list.
    half_time_home: int | None = None
    half_time_away: int | None = None


class Match(ApiModel):
    id: str
    competition_id: str
    season_id: str
    matchday: int | None = None
    stage_name: str | None = None
    group_label: str | None = None
    utc_date: datetime
    home_team: TeamRef
    away_team: TeamRef
    status: str
    score: Score | None = None
    odds_available: bool = False
    live_odds_available: bool = False
    xg_available: bool = False


class MatchDetail(Match):
    venue: Venue | None = None
    referee: TeamRef | None = None


class StatPair(ApiModel):
    home: float | None = None
    away: float | None = None


class StatSplit(ApiModel):
    all: StatPair | None = None
    first_half: StatPair | None = None
    second_half: StatPair | None = None


class MatchStats(ApiModel):
    match_id: str
    # Keyed by stat name (total_shots, shots_on_target, ball_possession,
    # corner_kicks, expected_goals, ...) — deliberately a generic dict rather
    # than named fields, since the provider exposes far more stat categories
    # (attack/passes/duels/defending/goalkeeping) than this pipeline uses and
    # new ones shouldn't break parsing. Values are Optional: the provider
    # sends an explicit `null` (not just an omitted key) for a stat category
    # it has no data for on some matches.
    overview: dict[str, StatSplit | None] = {}


class BookmakerMatchOdds(ApiModel):
    bookmaker: str
    # Raw, un-modeled: nesting order varies by market (total_goals is
    # line -> selection; asian_handicap is selection -> line; match_odds/btts
    # are flat selection -> {opening, last_seen}). Flattened generically in
    # orchestration.mapping rather than forced into one pydantic shape.
    markets: dict[str, dict] = {}


class MatchOdds(ApiModel):
    match_id: str
    bookmakers: list[BookmakerMatchOdds] = []


class LineupPlayer(ApiModel):
    id: str
    position: str | None = None
    jersey_number: int | None = None


class LineupTeam(ApiModel):
    formation: str | None = None
    starting_xi: list[LineupPlayer] = []
    substitutes: list[LineupPlayer] = []


class Lineup(ApiModel):
    match_id: str
    confirmed: bool = False
    home: LineupTeam | None = None
    away: LineupTeam | None = None


class TimelineEvent(ApiModel):
    sequence: int | None = None
    minute: int | None = None
    type: str
    team: TeamRef | None = None
    player: TeamRef | None = None


class Timeline(ApiModel):
    events: list[TimelineEvent] = []
    coverage: str | None = None
    reason: str | None = None


class InjurySuspensionEntry(ApiModel):
    player_id: str
    type: str
    status: str | None = None


class TeamInjuriesSuspensions(ApiModel):
    injuries: list[InjurySuspensionEntry] = []
    suspensions: list[InjurySuspensionEntry] = []
