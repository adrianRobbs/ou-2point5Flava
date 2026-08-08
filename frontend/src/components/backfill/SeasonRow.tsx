import { useEffect, useRef, useState } from 'react'
import {
  checkCoverage,
  fetchJobStatus,
  triggerSync,
  type SeasonCoverageCheck,
  type SeasonCoverageSummary,
} from '../../api/adminClient'
import { formatDateShort } from '../../utils/time'
import './SeasonRow.css'

const POLL_INTERVAL_MS = 4000
const TERMINAL_STATUSES = new Set(['succeeded', 'failed'])

export function SeasonRow({ competitionId, season }: { competitionId: string; season: SeasonCoverageSummary }) {
  const [coverage, setCoverage] = useState<SeasonCoverageCheck | null>(null)
  const [checking, setChecking] = useState(false)
  const [jobId, setJobId] = useState<string | null>(null)
  const [jobStatus, setJobStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<number | null>(null)

  // Stops the poll on unmount (collapsing the competition row) - otherwise
  // it keeps hitting the job-status endpoint for a component nobody sees.
  useEffect(() => () => stopPolling(), [])

  function stopPolling() {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current)
      pollRef.current = null
    }
  }

  function handleCheck() {
    setChecking(true)
    setError(null)
    checkCoverage(competitionId, season.season_id)
      .then(setCoverage)
      .catch((e: Error) => setError(e.message))
      .finally(() => setChecking(false))
  }

  function handleSync() {
    setError(null)
    triggerSync(competitionId, season.season_id, season.year)
      .then((job) => {
        setJobId(job.id)
        setJobStatus(job.status)
        stopPolling()
        pollRef.current = window.setInterval(() => {
          fetchJobStatus(job.id)
            .then((status) => {
              setJobStatus(status.status)
              if (TERMINAL_STATUSES.has(status.status)) stopPolling()
            })
            .catch(() => stopPolling()) // a transient poll failure shouldn't loop forever
        }, POLL_INTERVAL_MS)
      })
      .catch((e: Error) => setError(e.message))
  }

  const syncDisabled = jobStatus !== null && !TERMINAL_STATUSES.has(jobStatus)

  return (
    <div className="season-row">
      <span className="season-row__name">{season.name}</span>
      <span className="season-row__count tabular-nums">{season.our_match_count} stored</span>

      <span className="season-row__coverage">
        {coverage ? (
          <span className={`coverage-tag coverage-tag--${coverage.status}`}>
            {coverage.our_count}/{coverage.expected_total} &middot; {coverage.status.replace('_', ' ')}
          </span>
        ) : (
          <button className="season-row__link" onClick={handleCheck} disabled={checking}>
            {checking ? 'Checking…' : 'Check completeness'}
          </button>
        )}
      </span>

      <button className="season-row__sync" onClick={handleSync} disabled={syncDisabled}>
        {syncDisabled ? 'Syncing…' : 'Sync'}
      </button>

      {jobId && <span className={`job-tag job-tag--${jobStatus}`}>job {jobStatus}</span>}

      {/* Derived from the fixture list itself - the provider gives no real
          season start/end date. For a season not fully scheduled yet, the
          later end reflects only what's been announced so far, which is
          exactly what "expected_total" being smaller than a full season
          means too - this line makes that legible instead of a bare number. */}
      {coverage?.earliest_kickoff && coverage.latest_kickoff && (
        <span className="season-row__range">
          {formatDateShort(coverage.earliest_kickoff)} – {formatDateShort(coverage.latest_kickoff)}
        </span>
      )}

      {error && <span className="season-row__error">{error}</span>}
    </div>
  )
}
