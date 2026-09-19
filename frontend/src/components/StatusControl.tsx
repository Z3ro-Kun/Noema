import { statusLabel } from '../lib/labels'
import type { LibraryStatus, UserWorkState } from '../types/api'

/**
 * Where a reader is with a work, and the one obvious next thing to do.
 *
 * Phase 1Z. The backend records more than this: `added`, `status_changed`,
 * `rating_changed`, `removed`, plus `times_started` and `times_completed`.
 * Exposing that as buttons would make a reader learn the event model to use
 * the product, so the UI offers the five states they actually think in and
 * lets the events fall out of them.
 *
 * Reconsumption is the clearest case. There is no "reconsume" action and
 * there is no second row: **starting something again after finishing it is
 * just moving back to "In progress"**, which is what the backend already
 * counts. So a completed work offers "Read it again", and the history picks
 * it up as a restart.
 *
 * The primary action changes with the state, so the obvious thing to do is
 * always the biggest control:
 *
 *     not held     Add to library
 *     planned      Start reading
 *     in progress  Mark completed
 *     on hold      Pick it back up
 *     completed    Read it again
 *     abandoned    Give it another go
 *
 * The full status list stays available beside it for everything else. Status
 * never touches the rating in either direction -- see `RatingControl`.
 */

const STATUSES: LibraryStatus[] = [
  'planned',
  'in_progress',
  'on_hold',
  'completed',
  'abandoned',
]

/** The one action worth making prominent, given where the reader is. */
function primaryAction(
  state: UserWorkState | null,
): { label: string; status: LibraryStatus } | null {
  if (!state || !state.in_library) return null

  switch (state.status) {
    case 'planned':
      return { label: 'Start reading', status: 'in_progress' }
    case 'in_progress':
      return { label: 'Mark completed', status: 'completed' }
    case 'on_hold':
      return { label: 'Pick it back up', status: 'in_progress' }
    case 'completed':
      // Not a new work and not a reset: the same row, started again.
      return { label: 'Read it again', status: 'in_progress' }
    case 'abandoned':
      return { label: 'Give it another go', status: 'in_progress' }
    default:
      return null
  }
}

interface StatusControlProps {
  title: string
  state: UserWorkState | null
  busy?: boolean
  onAdd: () => void
  onStatus: (status: LibraryStatus) => void
  onRemove: () => void
}

export default function StatusControl({
  title,
  state,
  busy = false,
  onAdd,
  onStatus,
  onRemove,
}: StatusControlProps) {
  const held = Boolean(state && state.in_library)
  const action = primaryAction(state)

  if (!held) {
    return (
      <div className="space-y-2">
        <button
          type="button"
          disabled={busy}
          onClick={onAdd}
          className="w-full rounded-lg border border-slate-500 bg-slate-100 px-3 py-2 text-sm font-medium text-slate-900 hover:bg-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300 disabled:opacity-50"
        >
          Add to library
        </button>
        {state && !state.in_library && (
          // Removal is soft. Saying so stops "Add" reading as "start over".
          <p className="text-xs text-slate-500">
            You had this before. Your{' '}
            {state.rating !== null ? `rating of ${state.rating}/10 and your ` : ''}
            history were kept.
          </p>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {action && (
        <button
          type="button"
          disabled={busy}
          onClick={() => onStatus(action.status)}
          className="w-full rounded-lg border border-slate-500 bg-slate-100 px-3 py-2 text-sm font-medium text-slate-900 hover:bg-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300 disabled:opacity-50"
        >
          {action.label}
        </button>
      )}

      <label className="block text-[11px] uppercase tracking-wide text-slate-500">
        Status
        <select
          value={state?.status ?? 'planned'}
          disabled={busy}
          aria-label={`Status for ${title}`}
          onChange={(event) => onStatus(event.target.value as LibraryStatus)}
          className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm normal-case tracking-normal text-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300 disabled:opacity-50"
        >
          {STATUSES.map((value) => (
            <option key={value} value={value}>
              {statusLabel(value)}
            </option>
          ))}
        </select>
      </label>

      <button
        type="button"
        disabled={busy}
        onClick={onRemove}
        aria-label={`Remove ${title} from your library`}
        className="w-full rounded border border-slate-700 px-2 py-1 text-xs text-slate-400 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300 disabled:opacity-50"
      >
        Remove from library
      </button>
    </div>
  )
}
