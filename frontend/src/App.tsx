import { useEffect, useState } from 'react'
import { fetchDates, fetchPredictions } from './api/client'
import type { PredictionsForDate } from './types'
import { DatePicker } from './components/DatePicker'
import { MatchList } from './components/MatchList'
import { ExportButton } from './components/ExportButton'
import { BackfillPage } from './components/backfill/BackfillPage'
import { CompetitionsPage } from './components/competitions/CompetitionsPage'
import './App.css'

type Theme = 'dark' | 'light'
type View = 'predictions' | 'backfill' | 'competitions'

function pickDefaultDate(dates: string[]): string {
  const today = new Date().toISOString().slice(0, 10)
  if (dates.includes(today)) return today
  return dates.find((d) => d >= today) ?? dates[dates.length - 1]
}

export default function App() {
  const [view, setView] = useState<View>('predictions')
  const [dates, setDates] = useState<string[] | null>(null)
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [data, setData] = useState<PredictionsForDate | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [theme, setTheme] = useState<Theme | null>(() => localStorage.getItem('theme') as Theme | null)

  useEffect(() => {
    if (theme) document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  useEffect(() => {
    fetchDates()
      .then((res) => {
        setDates(res.dates)
        if (res.dates.length > 0) setSelectedDate(pickDefaultDate(res.dates))
      })
      .catch(() => setError('Could not load available dates.'))
  }, [])

  useEffect(() => {
    if (!selectedDate) return
    setLoading(true)
    setError(null)
    fetchPredictions(selectedDate)
      .then(setData)
      .catch(() => setError('Could not load predictions for this date.'))
      .finally(() => setLoading(false))
  }, [selectedDate])

  function toggleTheme() {
    const current = theme ?? (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')
    const next: Theme = current === 'dark' ? 'light' : 'dark'
    localStorage.setItem('theme', next)
    setTheme(next)
  }

  const backedCount = data?.predictions.filter((p) => p.call === 'BACK_FAVOURITE').length ?? 0

  return (
    <div className="app">
      <header className="app__header">
        <div>
          <p className="app__eyebrow">Over / Under 2.5</p>
          <h1 className="app__title">Matchday Decisions</h1>
        </div>
        <nav className="app__nav">
          <button
            className={`nav-link ${view === 'predictions' ? 'nav-link--active' : ''}`}
            onClick={() => setView('predictions')}
          >
            Matchday
          </button>
          <button
            className={`nav-link ${view === 'backfill' ? 'nav-link--active' : ''}`}
            onClick={() => setView('backfill')}
          >
            Backfill
          </button>
          <button
            className={`nav-link ${view === 'competitions' ? 'nav-link--active' : ''}`}
            onClick={() => setView('competitions')}
          >
            Competitions
          </button>
        </nav>
        <button className="theme-toggle" onClick={toggleTheme} aria-label="Toggle color theme">
          {(theme ?? 'dark') === 'dark' ? '☀' : '☾'}
        </button>
      </header>

      {view === 'backfill' ? (
        <BackfillPage />
      ) : view === 'competitions' ? (
        <CompetitionsPage />
      ) : (
        <>
          {dates && dates.length > 0 && selectedDate && (
            <DatePicker dates={dates} selected={selectedDate} onSelect={setSelectedDate} />
          )}

          {error && <p className="app__error">{error}</p>}

          {selectedDate && !error && (
            <section className="app__matchday">
              <div className="app__summary">
                <p>
                  {data ? (
                    <>
                      <strong>{data.total_assessed}</strong> assessed &middot; <strong>{backedCount}</strong> clear the rule
                    </>
                  ) : (
                    'Loading…'
                  )}
                </p>
                <ExportButton date={selectedDate} disabled={!data || data.predictions.length === 0} />
              </div>

              {loading && !data ? (
                <p className="app__loading">Loading matchday…</p>
              ) : (
                data && <MatchList predictions={data.predictions} totalAssessed={data.total_assessed} />
              )}
            </section>
          )}

          {dates && dates.length === 0 && (
            <p className="app__empty">No predictions recorded yet — run discover-fixtures.</p>
          )}
        </>
      )}
    </div>
  )
}
