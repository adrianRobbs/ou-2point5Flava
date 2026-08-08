import { useState } from 'react'
import { triggerDiscoverSync, triggerRefreshSync } from '../../api/adminClient'
import './DailySyncPanel.css'

// Stands in for the cron jobs commented out in render.yaml (see
// api/adminClient.ts) — click "Fetch upcoming fixtures" once a day
// (intended for shortly before midnight UTC, mirroring the cron schedule
// this replaces) and "Refresh odds & results" whenever you want the
// latest prices/results. Both run synchronously and can take up to
// ~15-20 seconds across all 13 tracked leagues — the buttons show a
// loading state rather than optimistically returning immediately.
export function DailySyncPanel() {
  const [days, setDays] = useState(2)
  const [discovering, setDiscovering] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const [discoverMessage, setDiscoverMessage] = useState<string | null>(null)
  const [refreshMessage, setRefreshMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  function handleDiscover() {
    setDiscovering(true)
    setError(null)
    setDiscoverMessage(null)
    triggerDiscoverSync(days)
      .then((result) => {
        const leagues = Object.keys(result.per_competition).length
        setDiscoverMessage(
          result.total_discovered === 0
            ? 'No new fixtures found in that window.'
            : `${result.total_discovered} new fixture(s) discovered across ${leagues} league(s).`,
        )
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setDiscovering(false))
  }

  function handleRefresh() {
    setRefreshing(true)
    setError(null)
    setRefreshMessage(null)
    triggerRefreshSync()
      .then((result) => {
        setRefreshMessage(
          `${result.total_refreshed} refreshed, ${result.total_backed} clear the rule, ` +
            `${result.total_results_synced} result(s) synced.`,
        )
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setRefreshing(false))
  }

  return (
    <section className="daily-sync">
      <p className="daily-sync__intro">
        Manual stand-in for the scheduled jobs, while the web service runs on the free tier.
      </p>
      <div className="daily-sync__actions">
        <div className="daily-sync__action">
          <button className="daily-sync__button" onClick={handleDiscover} disabled={discovering}>
            {discovering ? 'Fetching…' : 'Fetch upcoming fixtures'}
          </button>
          <label className="daily-sync__days">
            within
            <input
              type="number"
              min={1}
              max={7}
              value={days}
              onChange={(e) => setDays(Math.max(1, Math.min(7, Number(e.target.value) || 1)))}
              className="daily-sync__days-input tabular-nums"
            />
            day(s)
          </label>
          {discoverMessage && <span className="daily-sync__message">{discoverMessage}</span>}
        </div>

        <div className="daily-sync__action">
          <button className="daily-sync__button" onClick={handleRefresh} disabled={refreshing}>
            {refreshing ? 'Refreshing…' : 'Refresh odds & results'}
          </button>
          {refreshMessage && <span className="daily-sync__message">{refreshMessage}</span>}
        </div>
      </div>
      {error && <p className="app__error">{error}</p>}
    </section>
  )
}
