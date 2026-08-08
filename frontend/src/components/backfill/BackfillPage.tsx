import { useEffect, useState } from 'react'
import { fetchTrackedCompetitions, type TrackedCompetition } from '../../api/adminClient'
import { CompetitionRow } from './CompetitionRow'
import { DailySyncPanel } from './DailySyncPanel'
import './BackfillPage.css'

export function BackfillPage() {
  const [competitions, setCompetitions] = useState<TrackedCompetition[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expandedId, setExpandedId] = useState<string | null>(null)

  useEffect(() => {
    fetchTrackedCompetitions()
      .then(setCompetitions)
      .catch((e: Error) => setError(e.message))
  }, [])

  if (error) return <p className="app__error">{error}</p>
  if (!competitions) return <p className="app__loading">Loading competitions…</p>

  return (
    <section className="backfill-page">
      <DailySyncPanel />

      <p className="backfill-page__intro">
        The competitions this pipeline tracks, plus anything else discovered live. Expand one to see its
        seasons — how many matches we've stored, whether that's the full season, and a button to sync any gap.
      </p>
      <ul className="backfill-page__list">
        {competitions.map((competition) => (
          <CompetitionRow
            key={competition.competition_id}
            competition={competition}
            expanded={expandedId === competition.competition_id}
            onToggle={() =>
              setExpandedId(expandedId === competition.competition_id ? null : competition.competition_id)
            }
          />
        ))}
      </ul>
    </section>
  )
}
