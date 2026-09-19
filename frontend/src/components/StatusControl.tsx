import { completionCount, consumptionWords, statusLabel } from '../lib/labels'
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
 * Reconsumption used to be the clearest case, and was the one that was
 * wrong. A completed work's primary action was "Read it again", which moved
 * it back to "In progress"; finishing it again then counted a second
 * completion. That reads as a prompt -- the product asking whether you would
 * like to read it again -- and a reader who followed the prompt, or who used
 * the status list to look around, could find a work claiming two reads after
 * one. The count is supposed to say how many times someone *chose* to record
 * finishing this.
 *
 * Phase 1AA replaces the prompt with a statement and a deliberate control:
 *
 *     Completed
 *     [ - ]  Read 2 times  [ + ]   <- what the row says, and the two
 *     [ Read again ]                  things that change it
 *
 * The button posts one completion and nothing else moves it: no render, no
 * refresh, no status change, and no automatic question. The verb follows the
 * medium, because "read" an anime is wrong -- see `consumptionWords`.
 *
 * The primary action changes with the state, so the obvious thing to do is
 * always the biggest control:
 *
 *     not held     Add to library
 *     planned      Start reading
 *     in progress  Mark completed
 *     on hold      Pick it back up
 *     completed    (the count, and an explicit "Read again")
 *     abandoned    Give it another go
 *
 * The full status list stays available beside it for everything else. Status
 * never touches the rating in either direction -- see `RatingControl`, and
 * neither does recording another completion.
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
      // Nothing automatic here. Recording another completion is its own
      // explicit control below, so that finishing something can never be a
      // side effect of the page offering to start it again.
      return null
    case 'abandoned':
      return { label: 'Give it another go', status: 'in_progress' }
    default:
      return null
  }
}

interface StatusControlProps {
  title: string
  state: UserWorkState | null
  /** Chooses the verb: anime is watched, everything else is read. */
  domainSlug: string
  busy?: boolean
  onAdd: () => void
  onStatus: (status: LibraryStatus) => void
  onRemove: () => void
  /** Record one more completed cycle. Only ever called from the control. */
  onReconsume: () => void
  /** Take one back. Refused by the server below a count of one. */
  onUndoReconsume: () => void
}

export default function StatusControl({
  title,
  state,
  domainSlug,
  busy = false,
  onAdd,
  onStatus,
  onRemove,
  onReconsume,
  onUndoReconsume,
}: StatusControlProps) {
  const held = Boolean(state && state.in_library)
  const action = primaryAction(state)
  const completed = held && state !== null && state.status === 'completed'
  const words = consumptionWords(domainSlug)

  if (!held) {
    return (
      <div className="space-y-4">
        <button
          type="button"
          disabled={busy}
          onClick={onAdd}
          className="border-b border-accent pb-1 text-[0.9rem] text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
        >
          Add to library
        </button>
        {state && !state.in_library && (
          // Removal is soft. Saying so stops "Add" reading as "start over".
          <p className="max-w-sm text-[0.85rem] leading-relaxed text-paper-faint">
            You had this before. Your{' '}
            {state.rating !== null ? `rating of ${state.rating}/10 and your ` : ''}
            history were kept.
          </p>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-7">
      {action && (
        <button
          type="button"
          disabled={busy}
          onClick={() => onStatus(action.status)}
          className="border-b border-accent pb-1 text-[0.9rem] text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
        >
          {action.label}
        </button>
      )}

      {completed && state && (
        // The count as a plain statement with a control on either side.
        // Never phrased as a question: the product does not ask whether you
        // would like to read it again.
        <div className="space-y-4 border-t border-paper/10 pt-6">
          <div className="flex items-center gap-5">
            {/*
              Counting up was one-way, so a reader who pressed once too often
              was stuck with a number they had not meant to record. The floor
              is one: a work you finished has one reading to its name, and
              the correction on offer is "that extra cycle did not happen",
              never "I never read this". At one the control is disabled
              rather than hidden, so the pair does not move under the cursor.
            */}
            <button
              type="button"
              disabled={busy || state.times_completed <= 1}
              onClick={onUndoReconsume}
              aria-label={`Remove one recorded ${words.past.toLowerCase()} of ${title}`}
              className="flex h-9 w-9 items-center justify-center border border-paper/20 text-[1.1rem] text-paper-dim transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-30 disabled:hover:border-paper/20 disabled:hover:text-paper-dim"
            >
              <span aria-hidden="true">&minus;</span>
            </button>

            <p
              aria-live="polite"
              className="min-w-0 flex-1 font-display text-lg font-light text-paper"
            >
              {completionCount(state.times_completed, domainSlug)}
            </p>

            <button
              type="button"
              disabled={busy}
              onClick={onReconsume}
              aria-label={`Record another ${words.past.toLowerCase()} of ${title}`}
              className="flex h-9 w-9 items-center justify-center border border-paper/20 text-[1.1rem] text-paper-dim transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-30"
            >
              <span aria-hidden="true">+</span>
            </button>
          </div>

          <button
            type="button"
            disabled={busy}
            onClick={onReconsume}
            className="border-b border-accent pb-1 text-[0.9rem] text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
          >
            {words.again}
          </button>
          <p className="max-w-sm text-[0.85rem] leading-relaxed text-paper-faint">
            Records another finish, or takes one back if you recorded it by
            mistake. Your rating stays as it is either way.
          </p>
        </div>
      )}

      <label className="block text-[0.66rem] uppercase tracking-label text-paper-faint">
        Status
        <select
          value={state?.status ?? 'planned'}
          disabled={busy}
          aria-label={`Status for ${title}`}
          onChange={(event) => onStatus(event.target.value as LibraryStatus)}
          className="mt-3 block w-full max-w-xs appearance-none border-b border-paper/20 bg-transparent py-2 pr-6 text-[0.9rem] normal-case tracking-normal text-paper transition-colors duration-200 hover:border-paper/40 focus:border-accent focus-visible:outline-none disabled:opacity-50"
        >
          {STATUSES.map((value) => (
            <option key={value} value={value} className="bg-ink text-paper">
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
        className="border-b border-paper/20 pb-1 text-[0.8rem] text-paper-dim transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
      >
        Remove from library
      </button>
    </div>
  )
}
