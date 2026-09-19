import { useCoverImage } from '../lib/cover'
import type { ProductWork } from '../types/api'

/**
 * The 2:3 slot a cover would occupy.
 *
 * The Magic Patterns shelves were grids of poster artwork, and that artwork
 * carried most of the page's visual mass. Noema had none, and fabricating it
 * was the thing the product refused. Removing the slot entirely — which is
 * what the first pass did — removed the composition along with the pictures.
 *
 * Phase 1AA filled the slot for real: every work now carries a
 * `cover_image_url` its own source supplied. The plate below did not become
 * dead code, because a URL can fail to load and a future work may arrive
 * without artwork — see `useCoverImage`.
 *
 * So the slot stays, and when there is no artwork it is filled by a
 * *typographic plate*: the title set in the display serif on a tinted ink
 * field, inside a hairline frame, with the medium in small caps. It reads as
 * a deliberate editorial object — a spine, a title card — rather than as a
 * missing image. Nothing on it is invented: title, medium and year are the
 * work's own.
 *
 * **When real artwork arrives, it replaces the plate in the same slot.** The
 * grid, the stagger, the snap rail and every section height are identical
 * either way, so ingesting covers later is a data change and not a redesign.
 *
 * The tint is chosen by domain rather than at random, so the same work always
 * looks the same and the three media stay visually distinguishable — and it
 * is only a tint. Medium is printed in words on the plate as well, so nothing
 * here depends on colour alone.
 */

const DOMAIN_LABEL: Record<string, string> = {
  literature: 'Literature',
  anime: 'Anime',
  manhwa: 'Manga & Manhwa',
}

/**
 * One field per medium, each a short walk between `surface` and `ink` with a
 * different bias -- literature cooler, anime warmer toward the accent, manga
 * greener. No new hue enters: every stop is one of the four palette colours
 * nudged toward another. The medium is printed in words on the plate too, so
 * nothing here depends on telling the three apart by colour.
 */
const DOMAIN_FIELD: Record<string, string> = {
  literature:
    'bg-[linear-gradient(158deg,#3f4f44_0%,#334138_58%,#2c3930_100%)]',
  anime: 'bg-[linear-gradient(158deg,#4a4a3e_0%,#3a4038_58%,#2c3930_100%)]',
  manhwa: 'bg-[linear-gradient(158deg,#3a4f47_0%,#31423a_58%,#2c3930_100%)]',
}

/** Long titles need to step down or they overflow the plate. */
function titleSize(title: string): string {
  if (title.length > 44) return 'text-[0.95rem] leading-[1.25]'
  if (title.length > 26) return 'text-[1.1rem] leading-[1.2]'
  return 'text-[1.35rem] leading-[1.15]'
}

interface WorkPlateProps {
  work: ProductWork
  /** Eager-load the one above the fold; lazy everywhere else. */
  priority?: boolean
}

export default function WorkPlate({ work, priority = false }: WorkPlateProps) {
  // Real artwork wins whenever a source supplied it, and stops winning the
  // moment it fails to load: `src` goes null and the plate below renders,
  // which is the same fallback a work with no cover at all gets.
  const cover = useCoverImage(work.cover_image_url)

  if (cover.src) {
    return (
      <div className="relative overflow-hidden bg-ink-soft">
        <img
          src={cover.src}
          onError={cover.onError}
          alt={`Cover artwork for ${work.title}`}
          loading={priority ? 'eager' : 'lazy'}
          className="aspect-[2/3] w-full object-cover transition-transform duration-300 group-hover:scale-[1.03]"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 ring-1 ring-inset ring-paper/10"
        />
      </div>
    )
  }

  const field = DOMAIN_FIELD[work.domain.slug] ?? DOMAIN_FIELD.literature

  return (
    <div
      className={`relative flex aspect-[2/3] w-full flex-col overflow-hidden p-4 transition-transform duration-300 group-hover:scale-[1.02] ${field}`}
    >
      {/* A rule across the head, the way a title card carries one. */}
      <div aria-hidden="true" className="h-px w-8 shrink-0 bg-accent/70" />

      {/*
        The title sits in the optical centre rather than being pushed apart
        from the footer by `justify-between`, which left two large voids on a
        tall plate. One block, centred, with the imprint beneath it.
      */}
      <div className="flex min-h-0 flex-1 items-center">
        <p className={`font-display font-light text-paper ${titleSize(work.title)}`}>
          {work.title}
        </p>
      </div>

      {/*
        Medium and year live here, so the caption below the plate does not
        repeat them. When real artwork replaces this plate, the caption takes
        them back -- see `WorkEntry`.
      */}
      <div className="shrink-0">
        <p className="text-[0.56rem] uppercase tracking-label text-paper-faint">
          {DOMAIN_LABEL[work.domain.slug] ?? work.domain.name}
          {work.year !== null && ` · ${work.year}`}
        </p>
      </div>

      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 ring-1 ring-inset ring-paper/10"
      />
    </div>
  )
}
