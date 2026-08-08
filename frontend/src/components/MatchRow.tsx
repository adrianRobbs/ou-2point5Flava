import type { Prediction } from '../types'
import { formatKickoffLocal, formatKickoffTitle } from '../utils/time'
import './MatchRow.css'

function OutcomeMark({ hit }: { hit: boolean | null }) {
  if (hit === null) return <span className="outcome outcome--pending">·</span>
  return hit ? <span className="outcome outcome--hit">HIT</span> : <span className="outcome outcome--miss">MISS</span>
}

interface Props {
  prediction: Prediction
  style?: React.CSSProperties
}

export function MatchRow({ prediction, style }: Props) {
  const isBacked = prediction.call === 'BACK_FAVOURITE'

  return (
    <li className="match-row" style={style}>
      <time className="match-row__kickoff tabular-nums" dateTime={prediction.kickoff_utc} title={formatKickoffTitle(prediction.kickoff_utc)}>
        {formatKickoffLocal(prediction.kickoff_utc)}
      </time>

      <span className="match-row__competition" title={prediction.competition_name ?? undefined}>
        {prediction.competition_name ?? '—'}
      </span>

      <div className="match-row__fixture">
        <span className="match-row__team">{prediction.home_team}</span>
        <span className="match-row__vs">v</span>
        <span className="match-row__team">{prediction.away_team}</span>
      </div>

      {isBacked ? (
        <span className="match-row__call">
          <span className="pill pill--back">BACK {prediction.bet_side}</span>
          <span className="match-row__odds tabular-nums">{prediction.bet_odds?.toFixed(2)}</span>
        </span>
      ) : (
        <span className="match-row__call match-row__call--waiting">awaiting price</span>
      )}

      <span className="match-row__zone">{isBacked ? prediction.decision_zone.replace(/_/g, ' ') : ''}</span>

      <OutcomeMark hit={prediction.hit} />
    </li>
  )
}
