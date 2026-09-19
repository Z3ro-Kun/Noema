import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Discover from './Discover'
import { setSessionToken } from '../api/library'
import {
  ANIME,
  FACETS,
  SEARCH_RESPONSE,
  WORK,
  listPage,
  presentation,
  userState,
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
    if (url.includes('/search/semantic')) return json(options.semantic ?? SEARCH_RESPONSE)
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
      account={null}
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
    expect(alert).toHaveTextContent('the catalogue is unavailable')
    expect(screen.queryByText('Cowboy Bebop')).not.toBeInTheDocument()
  })

  it('says when nothing matches, and offers a way back', async () => {
    renderPage({ page: listPage([], 0) })

    expect(await screen.findByText('No works match these filters.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Show everything' })).toBeInTheDocument()
  })

  it('renders a work card from the public contract alone', async () => {
    renderPage({ page: listPage([presentation(ANIME, userState({ rating: 8 }))]) })

    expect(await screen.findByText('Cowboy Bebop')).toBeInTheDocument()
    expect(screen.getByText('カウボーイビバップ')).toBeInTheDocument()
    expect(screen.getByText(/Anime · TV · 1998/)).toBeInTheDocument()
    expect(screen.getByText('Action')).toBeInTheDocument()
    expect(screen.getByText('Bounty hunters in space.')).toBeInTheDocument()
    // The reader's own state is marked, and marked as theirs.
    expect(screen.getByText(/In your library · Planned · rated 8\/10/)).toBeInTheDocument()
  })

  it('states an absent synopsis rather than hiding it', async () => {
    renderPage({ page: listPage([presentation(WORK)]) })

    expect(await screen.findByText('No synopsis available')).toBeInTheDocument()
  })

  it('never renders corpus internals', async () => {
    const { container } = renderPage()

    await screen.findByText('Cowboy Bebop')
    const rendered = container.textContent ?? ''
    for (const forbidden of ['content_unit', 'embedding', 'adapter', 'extra_metadata']) {
      expect(rendered).not.toContain(forbidden)
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
        account={null}
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
    await user.click(screen.getByRole('button', { name: 'By meaning' }))

    expect(screen.getByText(/Results are text similarity, not a recommendation/)).toBeInTheDocument()
    expect(await screen.findByText(/Describe what you are in the mood for/)).toBeInTheDocument()
  })

  it('runs a semantic search and names where each passage came from', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByText('Cowboy Bebop')
    await user.click(screen.getByRole('button', { name: 'By meaning' }))
    await user.type(screen.getByLabelText('Describe a theme'), 'isolation')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    expect(
      await screen.findByText(/Alice was beginning to get very tired of sitting/),
    ).toBeInTheDocument()
    // A third-party summary is not the work's own text, and says so.
    expect(screen.getByText(/from a wikipedia summary, not the work’s own text/)).toBeInTheDocument()
    expect(screen.getByText(/from the work’s own text/)).toBeInTheDocument()
  })

  it('shows no similarity numbers on the product surface', async () => {
    const user = userEvent.setup()
    const { container } = renderPage()

    await screen.findByText('Cowboy Bebop')
    await user.click(screen.getByRole('button', { name: 'By meaning' }))
    await user.type(screen.getByLabelText('Describe a theme'), 'isolation')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    await screen.findByText(/Alice was beginning to get very tired of sitting/)
    const rendered = container.textContent ?? ''
    expect(rendered).not.toContain('0.4212')
    expect(rendered).not.toContain('similarity 0')
  })

  it('does not call semantic search a recommendation', async () => {
    const user = userEvent.setup()
    const { container } = renderPage()

    await screen.findByText('Cowboy Bebop')
    await user.click(screen.getByRole('button', { name: 'By meaning' }))
    await user.type(screen.getByLabelText('Describe a theme'), 'isolation')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    await screen.findByText(/Alice was beginning to get very tired of sitting/)
    expect(screen.getByText(/They are not chosen for you/)).toBeInTheDocument()
    expect(container.textContent ?? '').not.toMatch(/recommended for you/i)
  })

  it('links to the retrieval-detail surface rather than inlining the numbers', async () => {
    const user = userEvent.setup()
    const inspect = vi.fn()
    renderPage({}, { onOpenRetrievalDetail: inspect })

    await screen.findByText('Cowboy Bebop')
    await user.click(screen.getByRole('button', { name: 'By meaning' }))
    await user.type(screen.getByLabelText('Describe a theme'), 'isolation')
    await user.click(screen.getByRole('button', { name: 'Search' }))

    await user.click(await screen.findByRole('button', { name: 'Inspect retrieval details' }))
    expect(inspect).toHaveBeenCalled()
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
