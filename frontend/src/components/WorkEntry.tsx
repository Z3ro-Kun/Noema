import type { ReactNode } from 'react'
import WorkPlate from './WorkPlate'
import type { ProductWork, UserWorkState } from '../types/api'

/**
 * A work in a shelf, composed the way the Magic Patterns prototype composed
 * one: a 2:3 plate, then the title, then a quiet metadata line.
 *
 * The prototype's slot held cover artwork. Noema has none, so `WorkPlate`
 * fills it with a typographic plate instead — the visual mass is preserved
 * without inventing a picture, and real artwork drops into the same slot the
 * moment a source supplies it.
 *
 * One consequence worth stating: when the plate is carrying the title, the
 * caption underneath does *not* repeat it. A title set twice, once large and
 * once again immediately below, is redundancy the prototype never had — it
 * only captioned the title because its plate was a photograph. So the caption
 * shows the title only when real artwork is present.
 *
 * ---
 *
 * Canonical and personal stay apart
 *
 * `work` is shared and identical for every reader; `state` belongs to one
 * person. The prototype merged them, putting reading position in the same
 * metadata line as the creator. Here anything user-specific renders below a
 * rule, after the canonical facts — the separation `WorkPresentation` draws
 * in the API and the library tests enforce.
 */


interface WorkEntryProps {
  work: ProductWork
  /** This reader's own state, or null. Never another reader's. */
  state?: UserWorkState | null
  onOpen?: (workId: string) => void
  /** A user-specific line, e.g. "Currently watching". Rendered apart. */
  detail?: ReactNode
  priority?: boolean
  children?: ReactNode
}

/**
 * The caption under the plate, which never repeats what the plate says.
 *
 * A typographic plate already carries the title, the medium and the year, so
 * the caption is reduced to the credit. When real artwork takes the plate's
 * place it carries none of those, and the caption picks them all back up.
 */
function metaLine(work: ProductWork, plateCarriesFacts: boolean): string {
  const creator = work.creators[0]?.name
  if (plateCarriesFacts) return creator ?? ''

  return [creator, work.domain.name, work.media_format, work.year]
    .filter(Boolean)
    .join(' · ')
}

export default function WorkEntry({
  work,
  state,
  onOpen,
  detail,
  priority,
  children,
}: WorkEntryProps) {
  // The typographic plate carries title, medium and year; real artwork does
  // not, so the caption changes shape with it.
  const captionCarriesTitle = Boolean(work.cover_image_url)
  const held = Boolean(state && state.in_library)
  const meta = metaLine(work, !captionCarriesTitle)

  const body = (
    <>
      <WorkPlate work={work} priority={priority} />

      {captionCarriesTitle && (
        <p className="mt-3 font-display text-[1.05rem] font-light leading-snug text-paper transition-colors duration-200 group-hover:text-accent">
          {work.title}
        </p>
      )}

      {meta && (
        <p
          className={`text-[0.66rem] uppercase tracking-label text-paper-faint ${
            captionCarriesTitle ? 'mt-1.5' : 'mt-2.5'
          }`}
        >
          {meta}
        </p>
      )}
    </>
  )

  return (
    <article className="min-w-0">
      {onOpen ? (
        <button
          type="button"
          onClick={() => onOpen(work.id)}
          // The plate carries the title, so the button is named by its
          // content; an explicit label keeps that announcement short.
          aria-label={`${work.title} — open this work`}
          className="group block w-full text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {body}
        </button>
      ) : (
        body
      )}

      {/* --- this reader's own half, fenced off by a rule ------------------ */}
      {(detail || held || children) && (
        <div className="mt-3 border-t border-paper/10 pt-2.5">
          {detail && <div className="text-[0.76rem] text-paper-dim">{detail}</div>}
          {!detail && held && state && (
            <p className="text-[0.76rem] text-paper-dim">
              In your library
              {state.rating !== null && ` · rated ${state.rating}/10`}
            </p>
          )}
          {children}
        </div>
      )}
    </article>
  )
}
