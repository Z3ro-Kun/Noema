import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { WorkCover } from './WorkCard'
import WorkPlate from './WorkPlate'
import { completionCount, consumptionWords } from '../lib/labels'
import type { ProductWork } from '../types/api'

/**
 * Cover art, and the words for a medium.
 *
 * Phase 1AA. Covers now come from AniList and Project Gutenberg, which means
 * two things are true at once: most works have artwork, and the product
 * renders URLs it does not control. Both surfaces that draw a cover are
 * asserted here on the same three cases --
 *
 *     a URL that works      the artwork, with an accessible name
 *     no URL at all         the surface's own fallback
 *     a URL that fails      the same fallback, never a broken-image icon
 *
 * -- because a fallback that only exists for the null case leaves the third
 * one showing the browser's torn page, which is worse than no picture.
 */

function work(overrides: Partial<ProductWork> = {}): ProductWork {
  return {
    id: 'work-1',
    title: 'Frankenstein',
    original_title: null,
    domain: { slug: 'literature', name: 'Literature' },
    synopsis: null,
    cover_image_url: null,
    genres: [],
    concepts: [],
    creators: [],
    media_format: null,
    year: 1818,
    source: 'gutenberg',
    ...overrides,
  } as ProductWork
}

const COVER = 'https://www.gutenberg.org/cache/epub/84/pg84.cover.medium.jpg'

describe('WorkPlate', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders the artwork a source supplied', () => {
    render(<WorkPlate work={work({ cover_image_url: COVER })} />)

    const image = screen.getByRole('img', { name: /Frankenstein/ })
    expect(image).toHaveAttribute('src', COVER)
  })

  it('names the image for the work it belongs to', () => {
    render(<WorkPlate work={work({ cover_image_url: COVER })} />)

    // Not the URL, and not "image": the title is what identifies it.
    expect(screen.getByAltText('Cover artwork for Frankenstein')).toBeInTheDocument()
  })

  it('falls back to the typographic plate when there is no cover', () => {
    render(<WorkPlate work={work()} />)

    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    // The title still reads, which is the point of the plate.
    expect(screen.getByText('Frankenstein')).toBeInTheDocument()
  })

  it('falls back to the plate when the cover fails to load', () => {
    render(<WorkPlate work={work({ cover_image_url: 'https://example.invalid/x.jpg' })} />)

    fireEvent.error(screen.getByRole('img'))

    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(screen.getByText('Frankenstein')).toBeInTheDocument()
  })

  it('lazy-loads covers that are not above the fold', () => {
    const { rerender } = render(<WorkPlate work={work({ cover_image_url: COVER })} />)
    expect(screen.getByRole('img')).toHaveAttribute('loading', 'lazy')

    rerender(<WorkPlate work={work({ cover_image_url: COVER })} priority />)
    expect(screen.getByRole('img')).toHaveAttribute('loading', 'eager')
  })

  it('never exposes the URL as text or as a link', () => {
    const { container } = render(<WorkPlate work={work({ cover_image_url: COVER })} />)

    expect(container.textContent).not.toContain('gutenberg.org')
    expect(container.querySelector('a')).toBeNull()
  })
})

describe('WorkCover', () => {
  it('renders the artwork when there is some', () => {
    render(<WorkCover work={work({ cover_image_url: COVER })} />)

    expect(screen.getByAltText('Cover of Frankenstein')).toHaveAttribute('src', COVER)
  })

  it('renders the lettered tile when there is none', () => {
    const { container } = render(<WorkCover work={work()} />)

    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    // Decorative: the title is right beside it on every card that uses this.
    expect(container.querySelector('[aria-hidden="true"]')).toBeInTheDocument()
  })

  it('renders the tile when the artwork fails to load', () => {
    const { container } = render(
      <WorkCover work={work({ cover_image_url: 'https://example.invalid/x.jpg' })} />,
    )

    fireEvent.error(screen.getByRole('img'))

    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(container.querySelector('[aria-hidden="true"]')).toBeInTheDocument()
  })

  it('recovers when the work changes to one whose cover works', () => {
    // A grid re-using a row must not inherit the previous work's failure.
    const { rerender } = render(
      <WorkCover work={work({ cover_image_url: 'https://example.invalid/x.jpg' })} />,
    )
    fireEvent.error(screen.getByRole('img'))
    expect(screen.queryByRole('img')).not.toBeInTheDocument()

    rerender(<WorkCover work={work({ id: 'work-2', cover_image_url: COVER })} />)

    expect(screen.getByRole('img')).toHaveAttribute('src', COVER)
  })
})

describe('consumption vocabulary', () => {
  it('reads literature and manga, and watches anime', () => {
    expect(consumptionWords('literature').again).toBe('Read again')
    expect(consumptionWords('manhwa').again).toBe('Read again')
    expect(consumptionWords('anime').again).toBe('Watch again')
  })

  it('says the count even at one', () => {
    expect(completionCount(1, 'literature')).toBe('Read 1 time')
    expect(completionCount(2, 'literature')).toBe('Read 2 times')
    expect(completionCount(3, 'anime')).toBe('Watched 3 times')
  })

  it('falls back to reading for a domain it does not know', () => {
    // A new medium should read oddly, not crash or say nothing.
    expect(completionCount(2, 'podcast')).toBe('Read 2 times')
  })
})
