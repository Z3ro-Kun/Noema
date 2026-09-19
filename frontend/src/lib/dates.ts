import type { UserWorkState } from '../types/api'

/**
 * When things happened, as a reader would write them down.
 *
 * Established on the Library page and shared from here so the work page says
 * the same thing about the same row. Two surfaces printing a reader's own
 * dates by two slightly different rules is the kind of difference nobody
 * notices until it makes one of them look wrong.
 */

/** "12 Sep 2026" -- the format `WorkHistory` already uses. */
export function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

export interface RelationshipDate {
  label: string
  /** The stored ISO value, kept so a caller can sort or compare. */
  at: string
  formatted: string
}

/**
 * The dates worth printing, from the stored fields and nothing else.
 *
 * `started_at` is suppressed when it equals `completed_at`, which is what the
 * server stores for a work marked finished without ever being marked started:
 * "Started 19 Sep · Finished 19 Sep" states one event twice and implies a
 * reading that took no time.
 *
 * A work with no dates of its own falls back to when it was added, so a row
 * is never silent about a relationship that does exist. Nothing here is
 * inferred: every value is a column the server filled in.
 */
export function relationshipDates(state: UserWorkState): RelationshipDate[] {
  const dates: { label: string; at: string }[] = []

  if (state.started_at && state.started_at !== state.completed_at) {
    dates.push({ label: 'Started', at: state.started_at })
  }
  if (state.completed_at) dates.push({ label: 'Finished', at: state.completed_at })
  if (state.abandoned_at) dates.push({ label: 'Stopped', at: state.abandoned_at })
  if (dates.length === 0) dates.push({ label: 'Added', at: state.added_at })
  if (state.removed_at) dates.push({ label: 'Removed', at: state.removed_at })

  return dates.map((date) => ({ ...date, formatted: formatDate(date.at) }))
}
