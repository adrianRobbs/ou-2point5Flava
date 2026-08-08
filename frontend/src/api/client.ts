import type { DatesResponse, PredictionsForDate } from '../types'

async function getJSON<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) {
    throw new Error(`Request to ${url} failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export function fetchDates(): Promise<DatesResponse> {
  return getJSON<DatesResponse>('/api/dates')
}

export function fetchPredictions(date: string): Promise<PredictionsForDate> {
  return getJSON<PredictionsForDate>(`/api/predictions?date=${encodeURIComponent(date)}`)
}

// A URL, not a fetch call — the export button navigates the browser to this
// directly so the server's Content-Disposition header drives the download,
// rather than fetching a blob client-side and re-triggering one manually.
export function exportUrl(date: string): string {
  return `/api/predictions/export?date=${encodeURIComponent(date)}`
}
