import type { ReactNode } from 'react'
import { activityLine, statusLabel } from '../lib/labels'
import { formatDate } from '../lib/dates'
import type { ProductWork, UserWorkState } from '../types/api'

/**
 * One line of the archive ledger: a work, and the reader's relationship to it.
 *
 * The Stitch redesign replaced the grid of cards with a tabular ledger — full
 * width, hairline-ruled, four columns of different kinds of fact. It is the
 * page shape that the Library, Home's active-engagement section and the
 * taste dossier's exposure list all share, so it lives here rather than in
 * three places.
 *
 * ---
 *
 * The progress column, and why it says what it says
 *
 * The export shows this column as `Episode 14 of 22` over a filled bar, and
 * that is a genuinely good idea — it answers "how far am I?", which is the
 * question a library is for.
 *
 * **Noema cannot answer it yet, so it does not pretend to.** Two pieces are
 * missing and both are missing for every work in the corpus: no work records
 * how many episodes, chapters or volumes it has (the ingest adapters store
 * `format` and `status` and no unit counts), and no interaction records how
 * far through it a reader is (status, rating, dates and completion counts,
 * and nothing between "started" and "finished").
 *
 * A status of `in_progress` means the reader began it. It does not mean
 * episode 14, and rendering a bar from it would be inventing the number the
 * bar exists to communicate.
 *
 * So the column keeps its place in the composition and carries the truthful
 * relationship instead — what they did, whether they rated it, whether they
 * came back to it. When the data exists, this is the one component that
 * changes.
 */

interface WorkLedgerRowProps {
  work: ProductWork
  /** Null for a work not in the reader's library. */
  state: UserWorkState | null
  onOpen: (workId: string) => void
  /** A trailing control — Log entry, Restore, Not interested. */
  action?: ReactNode
  /**
   * Overrides the date column. The library shows when a work was last
   * engaged with; a shelf of suggestions has no date to show at all.
   */
  engaged?: { label: string; value: string } | null
  /** Dim the whole row: a removed entry is present but not active. */
  muted?: boolean
}

/** The last thing that actually happened, and when. Never a guess. */
function lastEngagement(state: UserWorkState): { label: string; value: string } | null {
  if (state.completed_at) return { label: 'Completed', value: formatDate(state.completed_at) }
  if (state.abandoned_at) return { label: 'Stopped', value: formatDate(state.abandoned_at) }
  if (state.started_at) return { label: 'Started', value: formatDate(state.started_at) }
  return { label: 'Added', value: formatDate(state.added_at) }
}

export default function WorkLedgerRow({
  work,
  state,
  onOpen,
  action,
  engaged,
  muted = false,
}: WorkLedgerRowProps) {
  const credited = work.creators.slice(0, 2).map((creator) => creator.name).join(', ')
  const when = engaged !== undefined ? engaged : state ? lastEngagement(state) : null

  return (
    <li
      className={`border-b border-paper/10 transition-colors duration-150 hover:bg-canvas-soft ${
        muted ? 'opacity-55' : ''
      }`}
    >
      <div className="grid gap-x-6 gap-y-3 px-1 py-4 md:grid-cols-12 md:items-baseline md:px-4">
        {/* --- medium ------------------------------------------------------ */}
        <div className="md:col-span-2">
          <p className="type-label text-accent-bright">{work.domain.name}</p>
          <p className="type-num mt-1 text-paper-faint">
            {[work.media_format, work.year].filter(Boolean).join(' · ') || '—'}
          </p>
        </div>

        {/* --- the work ---------------------------------------------------- */}
        <div className="md:col-span-4">
          <button
            type="button"
            onClick={() => onOpen(work.id)}
            aria-label={`${work.title} — open this work`}
            className="text-left font-display text-lg font-light leading-snug text-paper transition-colors duration-150 hover:text-accent-bright focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper"
          >
            {work.title}
          </button>
          {credited && <p className="type-body-sm mt-1 text-paper-faint">{credited}</p>}
        </div>

        {/* --- the reader's relationship ----------------------------------- */}
        <div className="md:col-span-3">
          {state ? (
            <>
              <p className="type-body text-paper">
                {activityLine(state, work.domain.slug)}
              </p>
              <p className="type-label mt-1 text-paper-faint">
                {statusLabel(state.status)}
                {state.times_completed > 1 && ` · ${state.times_completed} completions`}
              </p>
            </>
          ) : (
            <p className="type-body text-paper-faint">Not in your library</p>
          )}
        </div>

        {/* --- when ------------------------------------------------------- */}
        <div className="md:col-span-2">
          {when && (
            <>
              <p className="type-label text-paper-faint">{when.label}</p>
              <p className="type-num mt-1 text-paper-dim">{when.value}</p>
            </>
          )}
        </div>

        {action && <div className="md:col-span-1 md:text-right">{action}</div>}
      </div>
    </li>
  )
}

/**
 * The ledger's column heads.
 *
 * Hidden below the medium breakpoint, where the rows stack and each field
 * carries its own label instead — a header row over stacked blocks labels
 * nothing.
 */
export function LedgerHead({ action = false }: { action?: boolean }) {
  return (
    <div className="hidden border-b border-paper/20 px-4 pb-2 md:grid md:grid-cols-12 md:gap-x-6">
      <p className="type-label col-span-2 text-paper-faint">Medium</p>
      <p className="type-label col-span-4 text-paper-faint">Work &amp; creator</p>
      <p className="type-label col-span-3 text-paper-faint">Your relationship</p>
      <p className="type-label col-span-2 text-paper-faint">Engaged</p>
      {action && <p className="type-label col-span-1 text-right text-paper-faint">Action</p>}
    </div>
  )
}
