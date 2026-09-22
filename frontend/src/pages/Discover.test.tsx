import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Discover from './Discover'
import { setSessionToken } from '../api/client'
import { resetSessionForTests } from '../auth/session'
import {
  ANIME,
  FACETS,
  WORK,
  listPage,
  presentation,
  userState,
  workSearchResponse,
} from '../test/fixtures'

/**
 * Discover.
 *
 * Two things are under test, and the second is the one that matters.
 *
 * The first is ordinary: filters, paging, search, and the three states every
 * network-backed page has. Filtering must reach the *server* -- the corpus is
 * small enough to filter in React today and that will stop being true, so the
 * assertions check the request that went out, not just the list that came
 * back.
 *
 * The second is that Discover is search and not recommendation. Lexical and
 * semantic search are different questions with different answers, the page
 * says which one it is asking, and neither is dressed up as "for you".
 */

const WORKS = [presentation(WORK), presentation(ANIME)]

interface Options {
  page?: unknown
  facets?: unknown
  semantic?: unknown
  listFails?: boolean
  hangList?: boolean
  /** Semantic search answers with a server error. */
  semanticFails?: boolean
  /** Semantic search never reaches the server at all. */
  semanticUnreachable?: boolean
}

let requests: string[] = []

function mockApi(options: Options = {}) {
  requests = []
  return vi.fn((input: string | URL) => {
    const url = String(input)
    requests.push(url)
    const json = (body: unknown) =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response)

    if (url.includes('/works/facets')) return json(options.facets ?? FACETS)
    if (url.includes('/search/works')) {
      if (options.semanticUnreachable) {
        // What a dropped connection looks like to `fetch`.
        return Promise.reject(new TypeError('Failed to fetch'))
      }
      if (options.semanticFails) {
        return Promise.resolve({
          ok: false,
          status: 503,
          json: () => Promise.resolve({}),
        } as Response)
      }
      return json(options.semantic ?? workSearchResponse())
    }
    if (url.includes('/api/v1/works')) {
      if (options.hangList) return new Promise<Response>(() => {})
      if (options.listFails) {
        return Promise.resolve({
          ok: false,
          status: 503,
          json: () => Promise.resolve({ detail: 'the catalogue is unavailable' }),
        } as Response)
      }
      return json(options.page ?? listPage(WORKS))
    }
    return Promise.reject(new Error(`unexpected request: ${url}`))
  })
}

function renderPage(options: Options = {}, props: Record<string, unknown> = {}) {
  vi.stubGlobal('fetch', mockApi(options))
  return render(
    <Discover
      onNavigate={() => {}}
      onOpenWork={() => {}}
      onOpenRetrievalDetail={() => {}}
      {...props}
    />,
  )
}

/** The most recent request to the listing endpoint. */
function lastListRequest(): string {
  return [...requests].reverse().find((url) => url.includes('/api/v1/works?')) ?? ''
}

describe('Discover', () => {
  beforeEach(() => {
    resetSessionForTests()
    setSessionToken(null)
    vi.unstubAllGlobals()
  })

  // --- browsing ------------------------------------------------------------

  it('lists canonical works', async () => {
    renderPage()

    expect(await screen.findByText("Alice's Adventures in Wonderland")).toBeInTheDocument()
    expect(screen.getByText('Cowboy Bebop')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '2 works' })).toBeInTheDocument()
  })

  it('shows a loading state while works are being fetched', () => {
    renderPage({ hangList: true })

    expect(screen.getByText('Looking…')).toBeInTheDocument()
  })

  it('reports a failure instead of an empty list', async () => {
    renderPage({ listFails: true })

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The catalogue is unavailable.')
    expect(screen.queryByText('Cowboy Bebop')).not.toBeInTheDocument()
  })

  it('says when nothing matches, and offers a way back', async () => {
    renderPage({ page: listPage([], 0) })

    expect(await screen.findByText('No works match these filters.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show everything' })).toBeInTheDocument()
  })

  it('renders a grid entry from the public contract alone', async () => {
    renderPage({ page: listPage([presentation(ANIME, userState({ rating: 8 }))]) })

    expect(await screen.findByText('Cowboy Bebop')).toBeInTheDocument()
    // The plate carries the medium and the year; the caption carries the
    // credit, and neither is invented.
    expect(screen.getByText(/Anime · 1998/)).toBeInTheDocument()
    // The reader's own state is marked, and marked as theirs.
    expect(screen.getByText(/Planned to start, rated 8\/10/)).toBeInTheDocument()
  })

  it('shows nothing of the reader to an anonymous visitor', async () => {
    // The canonical half is identical either way; `user_state` is null.
    renderPage({ page: listPage([presentation(ANIME, null)]) })

    expect(await screen.findByText('Cowboy Bebop')).toBeInTheDocument()
    expect(screen.queryByText(/In your library/)).not.toBeInTheDocument()
    expect(screen.queryByText(/rated/)).not.toBeInTheDocument()
  })

  it('leaves synopsis and label chips to the work page', async () => {
    // A deliberate reduction, not an omission: the browse grid is for
    // looking, and a column of clamped descriptions under rows of chips is
    // what made this page read as a query interface. Both still appear in
    // full on WorkPage, which has its own tests for them.
    renderPage({ page: listPage([presentation(ANIME, userState({ rating: 8 }))]) })

    await screen.findByText('Cowboy Bebop')
    expect(screen.queryByText('Bounty hunters in space.')).not.toBeInTheDocument()
    expect(screen.queryByText('Action')).not.toBeInTheDocument()
  })

  it('never renders corpus internals', async () => {
    const { container } = renderPage()

    await screen.findByText('Cowboy Bebop')
    const rendered = container.textContent ?? ''
    for (const forbidden of ['content_unit', 'adapter', 'extra_metadata']) {
      expect(rendered).not.toContain(forbidden)
    }

    // The mode description used to name the mechanism ("using Noema's
    // embeddings"). It now says what the search does rather than how, so the
    // word should not appear anywhere on the page at all.
    for (const forbidden of ['embedding', 'semantic', 'vector', 'corpus', 'passage']) {
      expect(rendered.toLowerCase()).not.toContain(forbidden)
    }
  })

  it('opens a work when its card is clicked', async () => {
    const user = userEvent.setup()
    const open = vi.fn()
    renderPage({}, { onOpenWork: open })

    await user.click(await screen.findByText('Cowboy Bebop'))
    expect(open).toHaveBeenCalledWith('work-2')
  })

  // --- filtering -----------------------------------------------------------

  it('sends filters to the server rather than filtering in the client', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Cowboy Bebop')
    await user.selectOptions(screen.getByLabelText('Medium'), 'anime')

    await waitFor(() => expect(lastListRequest()).toContain('domain=anime'))
  })

  it('offers themes and genres from the server’s facets', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Cowboy Bebop')
    // Counts are shown, so a reader can see what a filter is worth.
    expect(screen.getByRole('option', { name: 'Tragedy (11)' })).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Theme'), 'psychological-depth')
    await waitFor(() => expect(lastListRequest()).toContain('concept=psychological-depth'))

    await user.selectOptions(screen.getByLabelText('Genre'), 'Mystery')
    await waitFor(() => expect(lastListRequest()).toContain('genre=Mystery'))
  })

  it('hides a filter that has nothing behind it', async () => {
    renderPage({ facets: { ...FACETS, genres: [] } })

    await screen.findByText('Cowboy Bebop')
    expect(screen.getByLabelText('Medium')).toBeInTheDocument()
    // An empty dropdown would claim coverage the corpus does not have.
    expect(screen.queryByLabelText('Genre')).not.toBeInTheDocument()
  })

  it('is honest that genres are the source’s and themes are Noema’s', async () => {
    renderPage()

    await screen.findByText('Cowboy Bebop')
    expect(screen.getByText(/literature works carry none/)).toBeInTheDocument()
  })

  it('still browses when the facets request fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: string | URL) => {
        const url = String(input)
        if (url.includes('/works/facets')) {
          return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) } as Response)
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(listPage(WORKS)),
        } as Response)
      }),
    )
    render(
      <Discover
        onNavigate={() => {}}
        onOpenWork={() => {}}
        onOpenRetrievalDetail={() => {}}
      />,
    )

    expect(await screen.findByText('Cowboy Bebop')).toBeInTheDocument()
    expect(screen.queryByLabelText('Medium')).not.toBeInTheDocument()
  })

  it('applies a filter chosen elsewhere in the product', async () => {
    renderPage({}, { initialConcept: 'psychological-depth' })

    await waitFor(() => expect(lastListRequest()).toContain('concept=psychological-depth'))
  })

  // --- paging --------------------------------------------------------------

  it('pages through a result set larger than one page', async () => {
    const user = userEvent.setup()
    renderPage({ page: listPage(WORKS, 30) })

    expect(await screen.findByText('Showing 1–12 of 30')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()

    await user.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(lastListRequest()).toContain('page=2'))
  })

  it('does not offer paging when everything fits on one page', async () => {
    renderPage({ page: listPage(WORKS, 2) })

    await screen.findByText('Cowboy Bebop')
    expect(screen.queryByRole('navigation', { name: 'Pagination' })).not.toBeInTheDocument()
  })

  // --- title search --------------------------------------------------------

  it('searches titles lexically, on the server', async () => {
    const user = userEvent.setup()
    renderPage({ page: listPage([presentation(WORK)], 1) })

    await screen.findByText("Alice's Adventures in Wonderland")
    await user.type(screen.getByLabelText('Search titles'), 'alice')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    await waitFor(() => expect(lastListRequest()).toContain('q=alice'))
    expect(await screen.findByRole('heading', { name: /matching “alice”/ })).toBeInTheDocument()
  })

  it('says when a search matched nothing', async () => {
    const user = userEvent.setup()
    renderPage({ page: listPage([], 0) })

    await user.type(screen.getByLabelText('Search titles'), 'qwertyuiop')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    expect(await screen.findByText('No works matched your search.')).toBeInTheDocument()
  })

  it('does not send an empty search as a filter', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Cowboy Bebop')
    await user.type(screen.getByLabelText('Search titles'), '   ')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    await waitFor(() => expect(lastListRequest()).not.toContain('q='))
  })

  // --- semantic search -----------------------------------------------------

  it('offers meaning search as a separate mode, and says what it is', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Cowboy Bebop')
    await user.click(screen.getByRole('button', { name: 'By theme' }))

    expect(screen.getByText(/It is a way of finding things, not a recommendation/)).toBeInTheDocument()
    expect(await screen.findByText(/Describe what you are in the mood for/)).toBeInTheDocument()
  })

  it('returns works rather than a list of repeated passages', async () => {
    // Three passages of Alice matched; Alice is one result, not three.
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Cowboy Bebop')
    await user.click(screen.getByRole('button', { name: 'By theme' }))
    await user.type(screen.getByLabelText('Describe a theme'), 'isolation')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    expect(
      await screen.findByRole('heading', { name: /Works that read like/ }),
    ).toBeInTheDocument()
    expect(
      screen.getAllByRole('button', { name: /Alice.*open this work/ }),
    ).toHaveLength(1)
    // The passage is not part of the result list: it sits inside a
    // disclosure that starts closed, so the list shows works and the
    // evidence is there only if asked for.
    const excerpt = screen.getByText(/Alice was beginning to get very tired of sitting/)
    const disclosure = excerpt.closest('details')
    expect(disclosure).not.toBeNull()
    expect(disclosure).not.toHaveAttribute('open')
  })

  it('opens the canonical work page from a meaning result', async () => {
    const user = userEvent.setup()
    const open = vi.fn()
    renderPage({}, { onOpenWork: open })

    await screen.findByText('Cowboy Bebop')
    await user.click(screen.getByRole('button', { name: 'By theme' }))
    await user.type(screen.getByLabelText('Describe a theme'), 'isolation')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    await user.click(
      await screen.findByRole('button', { name: /Alice.*open this work/ }),
    )
    expect(open).toHaveBeenCalledWith('work-1')
  })

  it('keeps the supporting passage compact and behind a disclosure', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Cowboy Bebop')
    await user.click(screen.getByRole('button', { name: 'By theme' }))
    await user.type(screen.getByLabelText('Describe a theme'), 'isolation')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    const why = (await screen.findAllByText('Why it appeared'))[0]
    await user.click(why)

    expect(
      screen.getByText(/Alice was beginning to get very tired of sitting/),
    ).toBeInTheDocument()
    // The tier is stated, so a summary is never mistaken for the work.
    expect(screen.getByText(/from the work itself/)).toBeInTheDocument()
  })

  it('says "across the whole work" when the passage sits in no container', async () => {
    // A work-level summary describes the whole work, and the source gave it
    // no chapter or episode number. Printing one would be an invention.
    const user = userEvent.setup()
    renderPage({
      semantic: workSearchResponse([
        {
          ...presentation(ANIME),
          similarity: 0.51,
          distance: 0.49,
          representation: 'content_unit',
          evidence: {
            container_id: null,
            container_type: null,
            container_title: null,
            container_sequence_number: null,
            text_tier: 'summary',
            matching_passages: 1,
            excerpt: 'A crew of bounty hunters drift between jobs.',
            source_name: 'wikipedia',
            licence: 'CC-BY-SA-4.0',
          },
        },
      ]),
    })

    await screen.findByText('Cowboy Bebop')
    await user.click(screen.getByRole('button', { name: 'By theme' }))
    await user.type(screen.getByLabelText('Describe a theme'), 'isolation')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    const why = (await screen.findAllByText('Why it appeared'))[0]
    await user.click(why)

    expect(screen.getByText(/across the whole work/)).toBeInTheDocument()
    expect(screen.queryByText(/null/)).not.toBeInTheDocument()
    expect(screen.getByText(/from a wikipedia summary/)).toBeInTheDocument()
  })

  it('shows no similarity numbers on the product surface', async () => {
    const user = userEvent.setup()
    const { container } = renderPage()

    await screen.findByText('Cowboy Bebop')
    await user.click(screen.getByRole('button', { name: 'By theme' }))
    await user.type(screen.getByLabelText('Describe a theme'), 'isolation')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    await screen.findAllByText('Why it appeared')
    const rendered = container.textContent ?? ''
    expect(rendered).not.toContain('0.4212')
    expect(rendered).not.toContain('similarity 0')
  })

  it('does not call semantic search a recommendation', async () => {
    const user = userEvent.setup()
    const { container } = renderPage()

    await screen.findByText('Cowboy Bebop')
    await user.click(screen.getByRole('button', { name: 'By theme' }))
    await user.type(screen.getByLabelText('Describe a theme'), 'isolation')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    await screen.findAllByText('Why it appeared')
    expect(screen.getByText(/They are not chosen for you/)).toBeInTheDocument()
    expect(container.textContent ?? '').not.toMatch(/recommended for you/i)
  })

  it('links to the retrieval-detail surface rather than inlining the numbers', async () => {
    const user = userEvent.setup()
    const inspect = vi.fn()
    renderPage({}, { onOpenRetrievalDetail: inspect })

    await screen.findByText('Cowboy Bebop')
    await user.click(screen.getByRole('button', { name: 'By theme' }))
    await user.type(screen.getByLabelText('Describe a theme'), 'isolation')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    await user.click(await screen.findByRole('button', { name: 'See how this search works' }))
    expect(inspect).toHaveBeenCalled()
  })

  it('reports a failed meaning search in the product’s words, not the server’s', async () => {
    const user = userEvent.setup()
    const { container } = renderPage({ semanticFails: true })

    await screen.findByText('Cowboy Bebop')
    await user.click(screen.getByRole('button', { name: 'By theme' }))
    await user.type(screen.getByLabelText('Describe a theme'), 'isolation')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Something went wrong. Please try again.')
    // No status code, and no "semantic search failed with 503".
    expect(container.textContent ?? '').not.toContain('503')
  })

  it('reports an unreachable meaning search as a connection problem', async () => {
    const user = userEvent.setup()
    const { container } = renderPage({ semanticUnreachable: true })

    await screen.findByText('Cowboy Bebop')
    await user.click(screen.getByRole('button', { name: 'By theme' }))
    await user.type(screen.getByLabelText('Describe a theme'), 'isolation')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Could not reach Noema')
    // The raw exception never reaches a reader.
    expect(container.textContent ?? '').not.toContain('TypeError')
    expect(container.textContent ?? '').not.toContain('Failed to fetch')
  })

  it('hands its whole state to the retrieval surface', async () => {
    // Opening retrieval detail used to lose the search: the reader came back
    // to an empty catalogue. The state travels with them instead.
    const user = userEvent.setup()
    const inspect = vi.fn()
    renderPage({}, { onOpenRetrievalDetail: inspect })

    await screen.findByText('Cowboy Bebop')
    await user.click(screen.getByRole('button', { name: 'By theme' }))
    await user.selectOptions(screen.getByLabelText('Medium'), 'anime')
    await user.type(screen.getByLabelText('Describe a theme'), 'isolation')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    await user.click(await screen.findByRole('button', { name: 'See how this search works' }))

    expect(inspect).toHaveBeenCalledWith({
      mode: 'meaning',
      query: 'isolation',
      domain: 'anime',
      concept: '',
      genre: '',
      page: 1,
    })
  })

  it('resumes a saved state rather than starting over', async () => {
    renderPage(
      {},
      {
        initialState: {
          mode: 'titles',
          query: 'alice',
          domain: 'literature',
          concept: 'tragedy',
          genre: 'Drama',
          page: 1,
        },
      },
    )

    // The filters, the mode and the query are all back -- and the search box
    // shows what the results below it are answering.
    await waitFor(() => expect(lastListRequest()).toContain('q=alice'))
    expect(lastListRequest()).toContain('domain=literature')
    expect(lastListRequest()).toContain('concept=tragedy')
    expect(lastListRequest()).toContain('genre=Drama')
    expect((screen.getByLabelText('Search titles') as HTMLInputElement).value).toBe('alice')
    expect(await screen.findByRole('heading', { name: /matching “alice”/ })).toBeInTheDocument()
  })

  it('starts clean when no state is handed back', async () => {
    // Direct navigation is unaffected: no resume, no leftover query.
    renderPage()

    await screen.findByText('Cowboy Bebop')
    expect((screen.getByLabelText('Search titles') as HTMLInputElement).value).toBe('')
    expect(lastListRequest()).not.toContain('q=')
    expect(await screen.findByRole('heading', { name: '2 works' })).toBeInTheDocument()
  })

  // --- discovery is not personalised ---------------------------------------

  it('never labels browsing results as chosen for the reader', async () => {
    const { container } = renderPage({}, { account: 'reader@example.test' })

    await screen.findByText('Cowboy Bebop')
    const rendered = container.textContent ?? ''
    expect(rendered).not.toMatch(/recommended/i)
    expect(rendered).not.toMatch(/for you/i)
    expect(screen.getByText(/everyone searching the same thing sees the same works/)).toBeInTheDocument()
  })

  // --- accessibility -------------------------------------------------------

  it('uses one level-1 heading and labels its controls', async () => {
    renderPage()

    await screen.findByText('Cowboy Bebop')
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(screen.getByRole('group', { name: 'Search mode' })).toBeInTheDocument()
    expect(screen.getByLabelText('Search titles')).toBeInTheDocument()
    for (const button of screen.getAllByRole('button')) {
      expect(button).toHaveAccessibleName()
    }
  })
})
