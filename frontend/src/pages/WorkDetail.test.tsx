import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import WorkDetail from './WorkDetail'
import {
  ANIME,
  CHARACTERS,
  CONTAINER,
  EPISODE,
  EPISODE_NO_TEXT,
  RELATIONSHIPS,
  SUMMARY_UNITS,
  UNITS,
  WORK,
} from '../test/fixtures'

/**
 * The corpus viewer.
 *
 * Explicitly a **development** surface: it reads `/works/{id}/internal`,
 * containers and stored content units, and shows ingestion provenance that
 * the product contract keeps out of a reader's way. Phase 1Y gave the product
 * its own work page and left this one alone, reachable from it.
 *
 * These assertions moved here from the app-level suite when navigation
 * changed. What they check has not: the viewer must never present a
 * third-party summary as the work's own text, must state an absence rather
 * than render an empty list, and must show where a claim came from.
 */

function mockApi() {
  return vi.fn((input: string | URL) => {
    const url = String(input)
    const json = (body: unknown) =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response)

    if (url.includes('/entities')) return json(url.includes('work-2') ? CHARACTERS : [])
    if (url.includes('/relationships')) {
      return json(url.includes('work-2') ? RELATIONSHIPS : [])
    }
    if (url.includes('/content-units')) {
      if (url.includes('episode-1')) return json(SUMMARY_UNITS)
      if (url.includes('episode-2')) return json([])
      return json(UNITS)
    }
    if (url.includes('/containers')) {
      return json(url.includes('work-2') ? [EPISODE, EPISODE_NO_TEXT] : [CONTAINER])
    }
    if (url.includes('/internal')) return json(url.includes('work-2') ? ANIME : WORK)
    return Promise.reject(new Error(`unexpected request: ${url}`))
  })
}

describe('Corpus viewer', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', mockApi())
  })

  it('displays a work’s containers and their content units', async () => {
    const user = userEvent.setup()
    render(<WorkDetail workId="work-1" onBack={() => {}} />)

    expect(await screen.findByText('Down the Rabbit-Hole')).toBeInTheDocument()

    await user.click(screen.getByText('Down the Rabbit-Hole'))

    await waitFor(() => {
      expect(screen.getByText(/Alice was beginning to get very tired/)).toBeInTheDocument()
    })
    expect(screen.getByText(/So she considered in her own mind/)).toBeInTheDocument()
  })

  it('surfaces ingestion provenance, which the product surface does not', async () => {
    render(<WorkDetail workId="work-1" onBack={() => {}} />)

    expect(await screen.findByText('literature.plain_text')).toBeInTheDocument()
    expect(screen.getByText('https://www.gutenberg.org/ebooks/11')).toBeInTheDocument()
  })

  it('says plainly when an episode has no text at all', async () => {
    const user = userEvent.setup()
    render(<WorkDetail workId="work-2" onBack={() => {}} />)

    await user.click(await screen.findByText('Stray Dog Strut'))

    // Must state the absence, not render an empty passage list.
    expect(await screen.findByText(/No text is available/)).toBeInTheDocument()
    expect(screen.getByText(/no dialogue or script/)).toBeInTheDocument()
  })

  it('labels a Wikipedia summary as a summary, not the episode itself', async () => {
    const user = userEvent.setup()
    render(<WorkDetail workId="work-2" onBack={() => {}} />)

    await user.click(await screen.findByText('Asteroid Blues'))

    expect(await screen.findByText(/Spike and Jet head to the Tijuana/)).toBeInTheDocument()
    // The reader must be able to tell this is not original dialogue.
    expect(screen.getByText('Summary')).toBeInTheDocument()
    expect(screen.getByText(/not original dialogue/)).toBeInTheDocument()
    expect(screen.getByText(/wikipedia/)).toBeInTheDocument()
    expect(screen.getByText(/CC-BY-SA-4.0/)).toBeInTheDocument()
    expect(screen.getByText(/Wikipedia contributors/)).toBeInTheDocument()
  })

  it('does not label literature passages as summaries', async () => {
    const user = userEvent.setup()
    render(<WorkDetail workId="work-1" onBack={() => {}} />)

    await user.click(await screen.findByText('Down the Rabbit-Hole'))

    expect(await screen.findByText(/Alice was beginning to get very tired/)).toBeInTheDocument()
    expect(screen.queryByText('Summary')).not.toBeInTheDocument()
    expect(screen.queryByText(/not original dialogue/)).not.toBeInTheDocument()
  })

  it('shows metadata, characters and source-stated relationships', async () => {
    render(<WorkDetail workId="work-2" onBack={() => {}} />)

    expect(await screen.findByText('Action')).toBeInTheDocument()
    expect(await screen.findByText('Spike Spiegel')).toBeInTheDocument()

    expect(screen.getByText('side story')).toBeInTheDocument()
    expect(screen.getByText('Cowboy Bebop: The Movie')).toBeInTheDocument()
    // The provenance of the claim must be visible, not just the claim.
    expect(screen.getByText('stated by anilist')).toBeInTheDocument()
  })
})
