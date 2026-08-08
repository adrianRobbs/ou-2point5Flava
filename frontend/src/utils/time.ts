// Grouping happens server-side by UTC calendar date (a deliberate v1
// simplification — see backend/webapp/service.py). Individual kickoff times
// are converted to the viewer's local time for display here, so a match
// can legitimately show a time that reads "into tomorrow" for viewers far
// enough east/west of UTC — the `title` attribute on the rendered time
// carries the full UTC instant so that's never a mystery.

export function formatKickoffLocal(iso: string): string {
  return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

export function formatKickoffTitle(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'full', timeStyle: 'short' })
}

export function formatDateShort(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export function formatRelativeTime(iso: string): string {
  const deltaMs = Date.now() - new Date(iso).getTime()
  const minutes = Math.round(deltaMs / 60_000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.round(hours / 24)
  return `${days}d ago`
}
