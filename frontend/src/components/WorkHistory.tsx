import type { HistoryEntry, HistoryKind, LibraryHistory } from '../types/api'

/**
 * What happened with a work, told rather than dumped.
 *
 * Phase 1Z. The backend keeps a full append-only event log; this shows the
 * handful of lines a reader would actually recognise as their own history:
 *
 *     Started      12 Sep
 *     Completed    15 Sep
 *     Rated 9/10   15 Sep
 *     Started again 3 Nov
 *
 * The API already speaks in those terms -- `kind` is a closed product
 * vocabulary derived from the events, not the event types themselves -- so
 * this file only chooses the wording and the date format. No event ids, no
 * `status_changed`, no before/after pairs, and no raw timestamps.
 *
 * Reconsumption shows up here on its own, without the word: a second
 * "Started again" followed by a second "Completed" is what a reader needs,
 * and neither requires them to know the model underneath.
 *
 * Phase 1AA removed the summary count that used to sit at the top of this
 * list. The status panel now states it outright -- "Read 2 times", beside
 * the control that changes it -- and the same number in two places is one
 * place too many: a reader should not have to work out whether they are
 * being told one thing or two.
 */

const KIND_LABELS: Record<HistoryKind, string> = {
  added: 'Added to your library',
  returned: 'Added back to your library',
  started: 'Started',
  restarted: 'Started again',
  completed: 'Completed',
  paused: 'Put on hold',
  abandoned: 'Abandoned',
  planned: 'Moved to planned',
  rated: 'Rated',
  rating_cleared: 'Rating removed',
  removed: 'Removed from your library',
}

/** "12 Sep 2026" -- a date someone recognises, not an ISO timestamp. */
function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleDateString(undefined, {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function label(entry: HistoryEntry): string {
  if (entry.kind === 'rated' && entry.rating !== null) {
    return `Rated ${entry.rating}/10`
  }
  return KIND_LABELS[entry.kind] ?? entry.kind
}

export default function WorkHistory({ history }: { history: LibraryHistory }) {
  if (history.entries.length === 0) return null

  return (
    <div>
      <p className="text-[0.66rem] uppercase tracking-label text-paper-faint">
        Your history with this
      </p>

      <div className="mt-5">
        <ol className="divide-y divide-paper/10 border-t border-paper/10">
          {history.entries.map((entry, index) => (
            <li
              key={`${entry.kind}-${entry.occurred_at}-${index}`}
              className="flex flex-wrap items-baseline justify-between gap-x-6 py-3"
            >
              <span className="font-display text-[1.05rem] font-light text-paper">
                {label(entry)}
              </span>
              <span className="text-[0.62rem] uppercase tracking-label text-paper-faint">
                {formatDate(entry.occurred_at)}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </div>
  )
}
