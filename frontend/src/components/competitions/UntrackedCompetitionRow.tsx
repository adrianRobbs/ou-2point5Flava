import { useState } from 'react'
import { trackCompetition, type CatalogEntry } from '../../api/adminClient'
import './UntrackedCompetitionRow.css'

interface Props {
  competition: CatalogEntry
  onTracked: (competitionId: string, tier: number | null) => void
}

// Tier here is purely informational grouping for later — it never gates
// anything operationally (see orchestration/catalog.py) — so it defaults
// to "none" rather than forcing a guess about a competition nobody has
// backtested yet.
export function UntrackedCompetitionRow({ competition, onTracked }: Props) {
  const [tier, setTier] = useState<string>('')
  const [tracking, setTracking] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function handleTrack() {
    setTracking(true)
    setError(null)
    const tierValue = tier === '' ? null : Number(tier)
    trackCompetition(competition.competition_id, tierValue)
      .then(() => onTracked(competition.competition_id, tierValue))
      .catch((e: Error) => setError(e.message))
      .finally(() => setTracking(false))
  }

  return (
    <li className="untracked-row">
      <span className="untracked-row__name">{competition.name}</span>
      <span className="untracked-row__type">{competition.type ?? ''}</span>
      <select
        className="untracked-row__tier-select"
        value={tier}
        onChange={(e) => setTier(e.target.value)}
        disabled={tracking}
      >
        <option value="">No tier</option>
        <option value="1">Tier 1</option>
        <option value="2">Tier 2</option>
        <option value="3">Tier 3</option>
      </select>
      <button className="untracked-row__track" onClick={handleTrack} disabled={tracking}>
        {tracking ? 'Tracking…' : 'Track'}
      </button>
      {error && <span className="untracked-row__error">{error}</span>}
    </li>
  )
}
