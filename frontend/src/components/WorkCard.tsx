import { useCoverImage } from '../lib/cover'
import type { ProductWork } from '../types/api'

/**
 * The cover slot, kept as its own primitive.
 *
 * This file used to hold the whole work card -- cover, title, credits,
 * synopsis and label chips -- and every surface rendered it. Each of the V1
 * passes replaced that with a composition suited to its own page: Discover
 * and Home use `WorkEntry`, the Library its own row, and the work page its
 * own spread, all drawing artwork through `WorkPlate`. The card itself had
 * no callers left and is gone.
 *
 * `WorkCover` stays. It is the small lettered fallback for a fixed-size
 * thumbnail, which is a different problem from `WorkPlate`'s 2:3 slot, and
 * it carries its own load-failure handling. Nothing renders it today; it is
 * kept as the primitive for the next surface that needs a thumbnail rather
 * than a poster.
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
 * Phase 1AA: real artwork, from each work's own source. A lettered tile
 * stands in when a source supplied none, and when a URL that was supplied
 * fails to load -- see `useCoverImage`. The tile is decorative either way:
 * the title is right beside it.
 */
export function WorkCover({
  work,
  size = 'md',
}: {
  work: ProductWork
  size?: 'sm' | 'md'
}) {
  const box = size === 'sm' ? 'h-16 w-12 text-sm' : 'h-24 w-16 text-lg'
  const cover = useCoverImage(work.cover_image_url)

  if (cover.src) {
    return (
      <img
        src={cover.src}
        onError={cover.onError}
        alt={`Cover of ${work.title}`}
        loading="lazy"
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
