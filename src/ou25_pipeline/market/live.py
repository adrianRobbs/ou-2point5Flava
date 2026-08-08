"""Fetches live pre-kickoff odds for upcoming matches and applies the
validated two-component rule from `decision.py` to produce real
BACK_FAVOURITE / NO_BET recommendations.

Bets are placed near kickoff by design (see `decision.py`'s module docstring
for why): the overdispersion signal needs the full 0.5-6.5 ladder priced and
mature, which an early, thin market may not have. `list_upcoming_matches`
therefore only returns matches the provider has already marked
`odds_available` — confirmed live to still carry `opening: null` even days
out, so there is no way to get an "opening" price from this provider; what
we read here is the live analogue of the `_close` prices the rule was
backtested on.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import Engine, text

from ou25_pipeline.api.client import StatsAPIClient
from ou25_pipeline.api.endpoints import get_match_odds, list_matches
from ou25_pipeline.market.decision import VALIDATED_COMPETITIONS, Decision, classify
from ou25_pipeline.market.ladder import LADDER_LINES, TARGET_LINE, devig_pair, fit_ladder, survival_curve
from ou25_pipeline.orchestration.mapping import map_odds_rows

# The historical backtest only ever observed one bookmaker (Bet365) — every
# threshold in decision.py was calibrated against that book's pricing. Live
# matches can carry several (confirmed: an upcoming MLS fixture had 4).
# Preferring Bet365 when present keeps a live decision consistent with what
# was actually backtested, rather than silently mixing in a differently
# priced book the rule has never been validated against.
PREFERRED_BOOKMAKER = "Bet365"


@dataclass(frozen=True)
class UpcomingMatch:
    match_id: str
    competition_id: str
    season_id: str
    kickoff_utc: datetime
    home_team_id: str
    away_team_id: str
    home_team: str
    away_team: str


def list_upcoming_matches(
    client: StatsAPIClient, competition_id: str, per_page: int = 50, max_days_ahead: int | None = None
) -> list[UpcomingMatch]:
    """Scheduled matches the provider has already started pricing.

    Filtering on `odds_available` is deliberate, not incidental — a match
    without it has no ladder to fit at all, and including it here would just
    push the empty case downstream instead of stating it at the source.

    `max_days_ahead` bounds by `kickoff_utc`, not by a provider-side filter
    (TheStatsAPI has no date-range parameter) — used by the manual sync
    button (webapp/routers/sync.py) to fetch "the next 1-3 days" on demand
    rather than everything currently priced, which for some leagues can
    reach weeks out. `None` (the default) keeps existing callers' behavior
    unchanged.
    """
    matches = list_matches(client, competition_id=competition_id, status="scheduled", per_page=per_page)
    cutoff = datetime.now(timezone.utc) + timedelta(days=max_days_ahead) if max_days_ahead is not None else None
    return [
        UpcomingMatch(m.id, m.competition_id, m.season_id, m.utc_date, m.home_team.id, m.away_team.id,
                     m.home_team.name, m.away_team.name)
        for m in matches
        if m.odds_available and (cutoff is None or m.utc_date <= cutoff)
    ]


def _pick_bookmaker_row(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    for row in rows:
        if row.get("bookmaker") == PREFERRED_BOOKMAKER:
            return row
    return rows[0]


def fetch_ladder_odds(client: StatsAPIClient, match_id: str) -> dict[float, tuple[float, float]] | None:
    """Reuses the same market-flattening logic the historical backfill
    stores through (`map_odds_rows`), so a live match is parsed identically
    to how every backtested one was — no separate, divergent parsing path."""
    odds = get_match_odds(client, match_id)
    row = _pick_bookmaker_row(map_odds_rows(match_id, odds))
    if row is None:
        return None

    result = {}
    for line in LADDER_LINES:
        tag = str(line).replace(".", "_")
        over, under = row.get(f"goals_{tag}_over_close"), row.get(f"goals_{tag}_under_close")
        if over and under:
            result[line] = (float(over), float(under))
    return result


def team_last_match_dates(engine: Engine, team_ids: list[str]) -> dict[str, datetime]:
    """Each team's most recent finished match kickoff, for the v3 layoff
    gate (`decision.LAYOFF_MAX_DAYS`). A team with no finished match at all
    is simply absent from the result — `classify_matches` passes None for
    the gap in that case, and the familiarity gate is what actually handles
    never-seen teams.
    """
    if not team_ids:
        return {}
    query = text("""
        SELECT team_id, MAX(kickoff_utc) AS last_kickoff FROM (
            SELECT home_team_id AS team_id, kickoff_utc FROM matches WHERE status = 'finished'
            UNION ALL
            SELECT away_team_id AS team_id, kickoff_utc FROM matches WHERE status = 'finished'
        ) t
        WHERE team_id = ANY(:team_ids)
        GROUP BY team_id
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {"team_ids": team_ids}).all()
    return {team_id: last for team_id, last in rows}


def team_known_match_counts(engine: Engine, team_ids: list[str]) -> dict[str, int]:
    """How many finished matches we hold for each team, as of now.

    Unlike `ladder.prior_match_counts` (point-in-time, per historical row)
    this counts against the *whole* finished-matches table, since a live
    recommendation is always being made after every match currently on
    record — there is no earlier point to be point-in-time relative to.
    """
    if not team_ids:
        return {}
    query = text("""
        SELECT team_id, COUNT(*) AS n FROM (
            SELECT home_team_id AS team_id FROM matches WHERE status = 'finished'
            UNION ALL
            SELECT away_team_id AS team_id FROM matches WHERE status = 'finished'
        ) t
        WHERE team_id = ANY(:team_ids)
        GROUP BY team_id
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {"team_ids": team_ids}).all()
    counts = {team_id: n for team_id, n in rows}
    return {team_id: counts.get(team_id, 0) for team_id in team_ids}


@dataclass(frozen=True)
class Recommendation:
    match: UpcomingMatch
    decision: Decision
    overdispersion: float
    min_prior_matches: int
    fav_prob: float


def classify_matches(client: StatsAPIClient, engine: Engine, upcoming: list[UpcomingMatch]) -> list[Recommendation]:
    """Fetch odds and apply the rule for an explicit list of matches.

    Split out from `recommend_for_competition` so a caller with its own
    source of `UpcomingMatch`es — `market/persistence.py`'s refresh pass
    reads them back from the `predictions` table rather than re-listing from
    the live API — can reuse the same fetch/fit/classify loop instead of a
    second, divergent copy of it.
    """
    if not upcoming:
        return []

    team_ids = sorted({m.home_team_id for m in upcoming} | {m.away_team_id for m in upcoming})
    known = team_known_match_counts(engine, team_ids)
    last_dates = team_last_match_dates(engine, team_ids)

    recommendations = []
    for match in upcoming:
        ladder_odds = fetch_ladder_odds(client, match.match_id)
        if not ladder_odds or TARGET_LINE not in ladder_odds:
            recommendations.append(Recommendation(
                match, Decision("NO_BET", "none", "", float("nan"), 0.0, skip_reason="no_odds_fetched"),
                float("nan"), 0, float("nan"),
            ))
            continue

        counts, survival = survival_curve(ladder_odds)
        fit = fit_ladder(counts, survival)
        quoted_over = devig_pair(*ladder_odds[TARGET_LINE])
        fav_side = "OVER" if quoted_over >= 0.5 else "UNDER"
        fav_prob = max(quoted_over, 1 - quoted_over)
        fav_odds = ladder_odds[TARGET_LINE][0] if fav_side == "OVER" else ladder_odds[TARGET_LINE][1]
        min_known = min(known.get(match.home_team_id, 0), known.get(match.away_team_id, 0))

        # v3 layoff gate input: gap between this kickoff and the LESS
        # recently active side's previous finished match — mirroring the
        # backtest's stale_gap definition exactly (measured at kickoff,
        # not at decision time, so a decision made days early classifies
        # the same as one made at the whistle).
        gaps = [
            (match.kickoff_utc - last_dates[t]).total_seconds() / 86400.0
            for t in (match.home_team_id, match.away_team_id) if t in last_dates
        ]
        stale_gap = max(gaps) if len(gaps) == 2 else None

        overdispersion = fit.overdispersion if fit else float("nan")
        decision = classify(
            overdispersion, min_known, fav_side, fav_odds,
            days_since_last_match=stale_gap,
            competition_validated=match.competition_id in VALIDATED_COMPETITIONS,
        )
        recommendations.append(Recommendation(match, decision, overdispersion, min_known, fav_prob))

    return recommendations


def recommend_for_competition(client: StatsAPIClient, engine: Engine, competition_id: str) -> list[Recommendation]:
    upcoming = list_upcoming_matches(client, competition_id)
    return classify_matches(client, engine, upcoming)


def recommendations_to_frame(recommendations: list[Recommendation]) -> pd.DataFrame:
    return pd.DataFrame([{
        "match_id": r.match.match_id,
        "competition_id": r.match.competition_id,
        "kickoff_utc": r.match.kickoff_utc,
        "home_team": r.match.home_team,
        "away_team": r.match.away_team,
        "overdispersion": r.overdispersion,
        "min_prior_matches": r.min_prior_matches,
        "fav_prob": r.fav_prob,
        "call": r.decision.call,
        "decision_zone": r.decision.zone,
        "bet_side": r.decision.side,
        "bet_odds": r.decision.odds,
        "edge_estimate": r.decision.edge_estimate,
        "skip_reason": r.decision.skip_reason,
        "rule_version": r.decision.rule_version,
        "logged_at": pd.Timestamp.now(tz="UTC"),
    } for r in recommendations])
