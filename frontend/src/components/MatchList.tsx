import type { Prediction } from '../types'
import { MatchRow } from './MatchRow'
import './MatchList.css'

interface Props {
  predictions: Prediction[]
  totalAssessed: number
}

// Empty is a real, distinct state from "no data" — the rule assessed
// matches and correctly found nothing worth backing, which is a finding,
// not a gap. See webapp/service.py's total_assessed for why that number
// exists at all.
export function MatchList({ predictions, totalAssessed }: Props) {
  if (predictions.length === 0) {
    return (
      <div className="match-list__empty">
        <p>
          {totalAssessed > 0
            ? `${totalAssessed} match${totalAssessed === 1 ? '' : 'es'} assessed — none cleared the rule.`
            : 'No fixtures discovered for this date yet.'}
        </p>
      </div>
    )
  }

  return (
    <div className="match-list">
      <div className="match-list__header" aria-hidden="true">
        <span>Kickoff</span>
        <span>Competition</span>
        <span>Fixture</span>
        <span>Call</span>
        <span>Zone</span>
        <span>Result</span>
      </div>
      <ul className="match-list__rows">
        {predictions.map((prediction, index) => (
          <MatchRow
            key={`${prediction.match_id}:${prediction.rule_version}`}
            prediction={prediction}
            style={{ animationDelay: `${index * 30}ms` }}
          />
        ))}
      </ul>
    </div>
  )
}
