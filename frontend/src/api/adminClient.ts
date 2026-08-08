const TOKEN_STORAGE_KEY = 'ou25_admin_token'

// Not a real login flow - this is a single-operator tool, and the backfill
// endpoints are the only authenticated routes anywhere in this app (see
// webapp/routers/backfill.py). The token is asked for once via a prompt and
// cached in localStorage; a 403 clears it so the next call re-prompts
// instead of looping on a stale/wrong value forever.
function getAdminToken(): string | null {
  let token = localStorage.getItem(TOKEN_STORAGE_KEY)
  if (!token) {
    token = window.prompt('Enter the backfill admin token:')
    if (token) localStorage.setItem(TOKEN_STORAGE_KEY, token)
  }
  return token
}

function clearAdminToken(): void {
  localStorage.removeItem(TOKEN_STORAGE_KEY)
}

async function adminFetch<T>(url: string, options: RequestInit = {}): Promise<T> {
  const token = getAdminToken()
  const response = await fetch(url, {
    ...options,
    headers: { ...options.headers, 'X-Admin-Token': token ?? '' },
  })
  if (response.status === 403) {
    clearAdminToken()
    throw new Error('Invalid admin token — try again.')
  }
  if (response.status === 501) {
    throw new Error('Backfill jobs are not configured on this deployment yet.')
  }
  if (!response.ok) {
    throw new Error(`Request to ${url} failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export interface TrackedCompetition {
  competition_id: string
  name: string | null
  tier: number | null
  is_registered: boolean
}

export interface SeasonCoverageSummary {
  season_id: string
  name: string
  year: string
  is_current: boolean
  our_match_count: number
}

export interface SeasonCoverageCheck {
  competition_id: string
  season_id: string
  expected_total: number
  our_count: number
  status: 'complete' | 'partial' | 'not_started'
  // Derived from the match list itself, not a real provider season
  // boundary (the provider has none) - for a season whose fixtures
  // aren't fully published yet, latest_kickoff is only what's been
  // announced so far, not necessarily the season's actual end.
  earliest_kickoff: string | null
  latest_kickoff: string | null
}

export interface JobHandle {
  id: string
  status: string
}

export function fetchTrackedCompetitions(): Promise<TrackedCompetition[]> {
  return adminFetch('/api/backfill/competitions')
}

export function fetchSeasons(competitionId: string): Promise<SeasonCoverageSummary[]> {
  return adminFetch(`/api/backfill/competitions/${competitionId}/seasons`)
}

export function checkCoverage(competitionId: string, seasonId: string): Promise<SeasonCoverageCheck> {
  return adminFetch(`/api/backfill/competitions/${competitionId}/seasons/${seasonId}/coverage`)
}

export function triggerSync(competitionId: string, seasonId: string, year: string): Promise<JobHandle> {
  const query = new URLSearchParams({ year })
  return adminFetch(`/api/backfill/competitions/${competitionId}/seasons/${seasonId}/sync?${query}`, {
    method: 'POST',
  })
}

export function fetchJobStatus(jobId: string): Promise<{ status: string }> {
  return adminFetch(`/api/backfill/jobs/${jobId}`)
}

// /api/sync/* — manual "run it now" stand-ins for the cron jobs commented
// out in render.yaml while the web service is on Render's free tier (no
// free option for Cron Jobs at all). These run synchronously in the
// request, not a Render Job, so unlike the backfill Sync button above they
// work regardless of plan — see webapp/routers/sync.py.

export interface DiscoverSyncResult {
  total_discovered: number
  per_competition: Record<string, number>
}

export interface RefreshSyncResult {
  total_refreshed: number
  total_backed: number
  total_results_synced: number
  per_competition: Record<
    string,
    { refreshed: number; backed: number; results_synced: number; awaiting_result: number }
  >
}

export function triggerDiscoverSync(maxDaysAhead: number): Promise<DiscoverSyncResult> {
  const query = new URLSearchParams({ max_days_ahead: String(maxDaysAhead) })
  return adminFetch(`/api/sync/discover?${query}`, { method: 'POST' })
}

export function triggerRefreshSync(): Promise<RefreshSyncResult> {
  return adminFetch('/api/sync/refresh', { method: 'POST' })
}

// /api/competitions/* — the full provider catalog (~150 competitions) and
// which ones we've deliberately chosen to track. Separate from
// `TrackedCompetition` above: this is every competition we know about,
// tracked or not, with the raw provider metadata (country/type) an
// untracked one still has even though it's never been backtested (see
// orchestration/catalog.py for why tier is null until someone studies it).

export interface CatalogEntry {
  competition_id: string
  name: string
  country: string | null
  type: string | null
  is_tracked: boolean
  tier: number | null
}

export function fetchAllCompetitions(): Promise<CatalogEntry[]> {
  return adminFetch('/api/competitions')
}

export function syncCompetitionCatalog(): Promise<{ synced: number }> {
  return adminFetch('/api/competitions/sync-catalog', { method: 'POST' })
}

export function trackCompetition(
  competitionId: string,
  tier: number | null,
): Promise<{ competition_id: string; is_tracked: boolean; tier: number | null }> {
  return adminFetch(`/api/competitions/${competitionId}/track`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tier }),
  })
}
