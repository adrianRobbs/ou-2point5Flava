import { useEffect, useState } from 'react'
import { fetchAllCompetitions, syncCompetitionCatalog, type CatalogEntry } from '../../api/adminClient'
import { UntrackedCompetitionRow } from './UntrackedCompetitionRow'
import './CompetitionsPage.css'

const TIER_LABELS: Record<string, string> = { '1': 'Tier 1', '2': 'Tier 2', '3': 'Tier 3', none: 'Unclassified' }

function groupBy<T>(items: T[], key: (item: T) => string): [string, T[]][] {
  const groups = new Map<string, T[]>()
  for (const item of items) {
    const k = key(item)
    groups.set(k, [...(groups.get(k) ?? []), item])
  }
  return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b))
}

export function CompetitionsPage() {
  const [competitions, setCompetitions] = useState<CatalogEntry[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [syncing, setSyncing] = useState(false)
  const [syncMessage, setSyncMessage] = useState<string | null>(null)

  function load() {
    fetchAllCompetitions()
      .then(setCompetitions)
      .catch((e: Error) => setError(e.message))
  }

  useEffect(load, [])

  function handleSyncCatalog() {
    setSyncing(true)
    setError(null)
    setSyncMessage(null)
    syncCompetitionCatalog()
      .then((result) => {
        setSyncMessage(`${result.synced} competition(s) synced from the provider.`)
        load()
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setSyncing(false))
  }

  function handleTracked(competitionId: string, tier: number | null) {
    setCompetitions((prev) =>
      prev?.map((c) => (c.competition_id === competitionId ? { ...c, is_tracked: true, tier } : c)) ?? prev,
    )
  }

  if (error) return <p className="app__error">{error}</p>
  if (!competitions) return <p className="app__loading">Loading competitions…</p>

  const tracked = competitions.filter((c) => c.is_tracked)
  const untracked = competitions.filter((c) => !c.is_tracked)
  const trackedByTier = groupBy(tracked, (c) => (c.tier === null ? 'none' : String(c.tier)))
  const untrackedByCountry = groupBy(untracked, (c) => c.country ?? 'Unknown')

  return (
    <section className="competitions-page">
      <section className="competitions-page__sync">
        <p className="competitions-page__sync-intro">
          Fetch the full list of competitions the provider offers (~150, cached here). Re-running this never
          changes which ones are already tracked — it only adds newly-seen competitions or refreshes names.
        </p>
        <div className="competitions-page__sync-action">
          <button className="competitions-page__sync-button" onClick={handleSyncCatalog} disabled={syncing}>
            {syncing ? 'Syncing…' : 'Sync catalog'}
          </button>
          {syncMessage && <span className="competitions-page__sync-message">{syncMessage}</span>}
        </div>
      </section>

      {competitions.length === 0 ? (
        <p className="app__empty">No competitions cached yet — run "Sync catalog" above.</p>
      ) : (
        <>
          <h2 className="competitions-page__heading">Tracked ({tracked.length})</h2>
          {tracked.length === 0 ? (
            <p className="app__empty">Nothing tracked yet — add one from the list below.</p>
          ) : (
            trackedByTier.map(([tier, group]) => (
              <div key={tier} className="competitions-page__group">
                <h3 className="competitions-page__group-heading">{TIER_LABELS[tier]}</h3>
                <ul className="competitions-page__tracked-list">
                  {group.map((c) => (
                    <li key={c.competition_id} className="competitions-page__tracked-item">
                      {c.name}
                    </li>
                  ))}
                </ul>
              </div>
            ))
          )}

          <h2 className="competitions-page__heading">Not tracked ({untracked.length})</h2>
          {untrackedByCountry.map(([country, group]) => (
            <div key={country} className="competitions-page__group">
              <h3 className="competitions-page__group-heading">{country}</h3>
              <ul className="competitions-page__untracked-list">
                {group.map((c) => (
                  <UntrackedCompetitionRow key={c.competition_id} competition={c} onTracked={handleTracked} />
                ))}
              </ul>
            </div>
          ))}
        </>
      )}
    </section>
  )
}
