import { exportUrl } from '../api/client'
import './ExportButton.css'

// A plain link, not a click handler that fetches a blob — letting the
// browser navigate to the export URL means the server's
// Content-Disposition header drives the download natively, no client-side
// blob/object-URL plumbing needed for something this simple.
export function ExportButton({ date, disabled }: { date: string; disabled?: boolean }) {
  return (
    <a
      className={`export-button ${disabled ? 'export-button--disabled' : ''}`}
      href={disabled ? undefined : exportUrl(date)}
      aria-disabled={disabled}
      download
    >
      Download CSV
    </a>
  )
}
