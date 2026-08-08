import './DatePicker.css'

interface Props {
  dates: string[] // YYYY-MM-DD, ascending
  selected: string
  onSelect: (date: string) => void
}

function chipLabel(isoDate: string): { weekday: string; day: string } {
  // Parsed as UTC noon, not midnight, so the displayed weekday/day can never
  // roll to the adjacent date under a negative UTC offset.
  const d = new Date(`${isoDate}T12:00:00Z`)
  return {
    weekday: d.toLocaleDateString(undefined, { weekday: 'short', timeZone: 'UTC' }).toUpperCase(),
    day: d.toLocaleDateString(undefined, { day: '2-digit', month: 'short', timeZone: 'UTC' }),
  }
}

// A horizontal strip of known dates for one-tap navigation between the days
// we actually have data for, plus a native date input as the escape hatch
// for jumping to an arbitrary date outside that list — including one with
// zero predictions, which is itself a legitimate thing to check.
export function DatePicker({ dates, selected, onSelect }: Props) {
  return (
    <div className="date-picker">
      <div className="date-picker__strip" role="tablist" aria-label="Select a matchday">
        {dates.map((date) => {
          const { weekday, day } = chipLabel(date)
          const isSelected = date === selected
          return (
            <button
              key={date}
              role="tab"
              aria-selected={isSelected}
              className={`date-chip ${isSelected ? 'date-chip--selected' : ''}`}
              onClick={() => onSelect(date)}
            >
              <span className="date-chip__weekday">{weekday}</span>
              <span className="date-chip__day">{day}</span>
            </button>
          )
        })}
      </div>
      <input
        type="date"
        className="date-picker__jump"
        value={selected}
        onChange={(e) => e.target.value && onSelect(e.target.value)}
        aria-label="Jump to a specific date"
      />
    </div>
  )
}
