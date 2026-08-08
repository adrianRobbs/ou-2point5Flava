// Mirrors backend/webapp/schemas.py — keep in sync by hand; there are only
// three response shapes, so a generator felt like more machinery than the
// surface area warrants.

export interface Prediction {
  match_id: string
  rule_version: string
  competition_id: string
  competition_name: string | null
  kickoff_utc: string // ISO 8601, always UTC
  home_team: string
  away_team: string
  overdispersion: number | null
  min_prior_matches: number | null
  fav_prob: number | null
  call: 'BACK_FAVOURITE' | 'NO_BET'
  decision_zone: string
  bet_side: 'OVER' | 'UNDER' | null
  bet_odds: number | null
  edge_estimate: number | null
  skip_reason: string | null
  status: 'pending' | 'updated'
  odds_updated_at: string | null
  fetch_count: number
  match_status: string | null
  actual_total_goals: number | null
  hit: boolean | null
}

export interface PredictionsForDate {
  date: string // YYYY-MM-DD
  total_assessed: number
  predictions: Prediction[]
}

export interface DatesResponse {
  dates: string[] // YYYY-MM-DD, ascending
}
