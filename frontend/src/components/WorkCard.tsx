import type { ReactNode } from 'react'
import { statusLabel } from '../lib/labels'
import type { ProductWork, UserWorkState } from '../types/api'

/**
 * A canonical work, as every product surface shows it.
 *
 * Phase 1Y lifted this out of the library page so discovery, the library and
 * the work page render a work the same way. One renderer is the point: a work
 * that looks different depending on where it appears reads as two works.
 *
 * Everything here comes from `ProductWork`, which is canonical and identical
 * for every viewer. Nothing user-specific is drawn inside the card body --
 * `state` only controls a small marker, and the controls that *change* that
 * state are passed in as `children` and rendered in their own fenced block.
 * The separation is structural, not stylistic: it is how a reader can always
 * tell "this is the work" from "this is you".
 *
 * Absences are stated rather than hidden. No source in the current corpus
 * recorded cover art, so the placeholder says what it is instead of showing a
 * guessed URL or a fabricated image.
 */

/** A domain letter for the coverless placeholder. Not an image. */
const DOMAIN_INITIAL: Record<string, string> = {
  literature: 'L',
  anime: 'A',
  manhwa: 'M',
}

/**
 * The cover slot.
 *
 * `cover_image_url` is null for every work Noema currently holds, because
 * neither ingestion path requested cover art. A lettered tile says so
 * honestly; guessing a CDN URL pattern would be fabricating metadata.
 */
export function WorkCover({
  work,
  size = 'md',
}: {
  work: ProductWork
  size?: 'sm' | 'md'
}) {
  const box = size === 'sm' ? 'h-16 w-12 text-sm' : 'h-24 w-16 text-lg'

  if (work.cover_image_url) {
    return (
      <img
        src={work.cover_image_url}
        alt={`Cover of ${work.title}`}
        className={`${box} shrink-0 rounded object-cover`}
      />
    )
  }

  return (
    <div
      className={`${box} flex shrink-0 items-center justify-center rounded border border-slate-800 bg-slate-900 font-semibold text-slate-600`}
      // Decorative: the title is right beside it, and "no cover available"
      // read out on every card would be noise rather than information.
      aria-hidden="true"
    >
      {DOMAIN_INITIAL[work.domain.slug] ?? '?'}
    </div>
  )
}

/** Canonical half: identical for every viewer, signed in or not. */
export function WorkFacts({
  work,
  conceptLimit = 6,
  synopsisLines = 3,
}: {
  work: ProductWork
  conceptLimit?: number
  synopsisLines?: number
}) {
  const subtitle = [work.domain.name, work.media_format, work.year]
    .filter(Boolean)
    .join(' · ')

  return (
    <div className="min-w-0 flex-1">
      <p className="truncate font-medium text-slate-100">{work.title}</p>
      {work.original_title && work.original_title !== work.title && (
        <p className="truncate text-xs text-slate-500">{work.original_title}</p>
      )}
      <p className="mt-0.5 text-xs text-slate-400">{subtitle}</p>

      {work.creators.length > 0 && (
        <p className="mt-1 text-xs text-slate-400">
          {work.creators.map((creator) => `${creator.name} (${creator.role})`).join(' · ')}
        </p>
      )}

      {work.synopsis ? (
        <p
          className={`mt-2 text-xs text-slate-400 ${
            synopsisLines === 3 ? 'line-clamp-3' : ''
          }`}
        >
          {work.synopsis}
        </p>
      ) : (
        // An absence, stated. No source supplied a description for this work.
        <p className="mt-2 text-xs italic text-slate-600">No synopsis available</p>
      )}

      {(work.genres.length > 0 || work.concepts.length > 0) && (
        <div className="mt-2 flex flex-wrap gap-1">
          {work.genres.map((genre) => (
            <span
              key={`genre-${genre}`}
              className="rounded bg-slate-800 px-1.5 py-0.5 text-[11px] text-slate-300"
            >
              {genre}
            </span>
          ))}
          {work.concepts.slice(0, conceptLimit).map((concept) => (
            <span
              key={concept.slug}
              title={`${concept.concept_type} (Noema vocabulary)`}
              className="rounded border border-slate-700 px-1.5 py-0.5 text-[11px] text-slate-400"
            >
              {concept.name}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

interface WorkCardProps {
  work: ProductWork
  /** The caller's own state, or null. Never another reader's. */
  state?: UserWorkState | null
  /** Opens the work. Omitted where the card is already the page. */
  onOpen?: (workId: string) => void
  /** Interaction controls, rendered in their own fenced block. */
  children?: ReactNode
}

export default function WorkCard({ work, state, onOpen, children }: WorkCardProps) {
  const inLibrary = Boolean(state && state.in_library)

  return (
    <article className="flex flex-wrap items-start gap-4 rounded-lg border border-slate-800 p-4 sm:flex-nowrap">
      <WorkCover work={work} />

      <div className="min-w-0 flex-1">
        {onOpen ? (
          <button
            type="button"
            onClick={() => onOpen(work.id)}
            className="block w-full text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
          >
            <WorkFacts work={work} />
          </button>
        ) : (
          <WorkFacts work={work} />
        )}

        {/*
          The one user-specific mark on the canonical card, and it is a word
          rather than a colour so it survives being read aloud or printed.
        */}
        {inLibrary && state && (
          <p className="mt-2 text-[11px] uppercase tracking-wide text-slate-500">
            In your library · {statusLabel(state.status)}
            {state.rating !== null && ` · rated ${state.rating}/10`}
          </p>
        )}
      </div>

      {children && <div className="w-full shrink-0 sm:w-auto">{children}</div>}
    </article>
  )
}
