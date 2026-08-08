import { useEffect, useState } from 'react'
import { fetchSeasons, type SeasonCoverageSummary, type TrackedCompetition } from '../../api/adminClient'
import { SeasonRow } from './SeasonRow'
import './CompetitionRow.css'

interface Props {
  competition: TrackedCompetition
  expanded: boolean
  onToggle: () => void
}

// Seasons are fetched lazily on first expand, not eagerly for every
// competition on page load — the seasons call costs one live API request
// (list_seasons), and there's no reason to spend that for a competition
// nobody's looking at yet.
export function CompetitionRow({ competition, expanded, onToggle }: Props) {
  const [seasons, setSeasons] = useState<SeasonCoverageSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (expanded && seasons === null && !error) {
      fetchSeasons(competition.competition_id)
        .then(setSeasons)
        .catch((e: Error) => setError(e.message))
    }
  }, [expanded, seasons, error, competition.competition_id])

  return (
    <li className="competition-row">
      <button className="competition-row__header" onClick={onToggle} aria-expanded={expanded}>
        <span className="competition-row__name">
          {competition.name ?? competition.competition_id}
          {!competition.is_registered && <span className="competition-row__flag">not yet registered</span>}
        </span>
        {competition.tier !== null && <span className="competition-row__tier">Tier {competition.tier}</span>}
      </button>

      {expanded && (
        <div className="competition-row__seasons">
          {error && <p className="app__error">{error}</p>}
          {!error && seasons === null && <p className="app__loading">Loading seasons…</p>}
          {seasons?.map((season) => (
            <SeasonRow key={season.season_id} competitionId={competition.competition_id} season={season} />
          ))}
        </div>
      )}
    </li>
  )
}
