import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import SemanticSearch from './SemanticSearch'
import { SEARCH_RESPONSE } from '../test/fixtures'

/**
 * The retrieval-inspection surface.
 *
 * Discover carries semantic search as a reader meets it -- a passage and where
 * it came from. This page is the other half: similarity values, distances,
 * the representation switch and the grouping configuration behind a derived
 * passage. It is for understanding what retrieval did, and Phase 1Y left it
 * exactly as it was, reachable from Discover.
 *
 * These assertions moved here from the app-level suite when navigation
 * changed. What they check has not: a similarity is labelled as a similarity,
 * a mixed-tier result says so, and a derived passage announces that it is
 * derived and names its sources.
 */

function mockSearch(body: unknown = SEARCH_RESPONSE) {
  return vi.fn((input: string | URL) => {
    const url = String(input)
    if (url.includes('/search/semantic')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(body),
      } as Response)
    }
    return Promise.reject(new Error(`unexpected request: ${url}`))
  })
}

async function search(user: ReturnType<typeof userEvent.setup>, query = 'isolation') {
  await user.type(screen.getByPlaceholderText(/Describe a theme/), query)
  await user.click(screen.getByRole('button', { name: 'Search' }))
}

describe('Semantic search (retrieval detail)', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', mockSearch())
  })

  it('labels results as similarity rather than as relationships', async () => {
    const user = userEvent.setup()
    render(<SemanticSearch onBack={() => {}} />)

    await search(user)

    expect(await screen.findByTestId('search-results')).toBeInTheDocument()
    expect(screen.getByText(/cosine similarity/)).toBeInTheDocument()
    expect(screen.getByText(/similarity 0.421/)).toBeInTheDocument()
    expect(screen.getByText(/Alice was beginning to get very tired/)).toBeInTheDocument()
  })

  it('warns that an unfiltered search mixes text tiers', async () => {
    const user = userEvent.setup()
    render(<SemanticSearch onBack={() => {}} />)

    await search(user)

    await screen.findByTestId('search-results')
    expect(screen.getByText(/Mixed text tiers/)).toBeInTheDocument()
    // Each hit says which tier and domain it came from.
    expect(screen.getByText('primary')).toBeInTheDocument()
    expect(screen.getByText('summary')).toBeInTheDocument()
    expect(screen.getByText(/source: wikipedia/)).toBeInTheDocument()
  })

  it('can switch to the contextual passage representation', async () => {
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      mockSearch({
        ...SEARCH_RESPONSE,
        representation: 'contextual_passage',
        hits: [
          {
            ...SEARCH_RESPONSE.hits[0],
            representation: 'contextual_passage',
            content_unit_id: null,
            passage_id: 'p1',
            source_unit_ids: ['u1', 'u2', 'u3'],
            unit_count: 3,
            first_unit_sequence: 1,
            last_unit_sequence: 3,
            grouping_config: 'window=3;overlap=1;max_tokens=240',
          },
        ],
      }),
    )
    render(<SemanticSearch onBack={() => {}} />)

    await user.type(screen.getByPlaceholderText(/Describe a theme/), 'isolation')
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Representation' }),
      'contextual_passage',
    )
    await user.click(screen.getByRole('button', { name: 'Search' }))

    await screen.findByTestId('search-results')
    // A derived passage must announce itself as derived and name its sources.
    expect(screen.getByText(/passage of 3 units/)).toBeInTheDocument()
    expect(screen.getByText(/derived from source units 1–3/)).toBeInTheDocument()
    expect(screen.getByText(/window=3;overlap=1;max_tokens=240/)).toBeInTheDocument()
  })
})
