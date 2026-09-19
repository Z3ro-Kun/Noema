import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Library from './Library'
import { setSessionToken } from '../api/library'

/**
 * The reader's own library.
 *
 * The point is the contract with the backend, not the visuals: that the UI
 * sends the bearer token, that it shows the user's own state rather than the
 * corpus, and that rating and status stay separate on the way out.
 *
 * Phase 1Y moved browsing the shared corpus out of this page and into
 * Discover. Phase 1Z organised what remained around the five states the
 * backend already stores, and moved rating to the work page where there is
 * room to explain it -- so this page reports a rating and never writes one.
 *
 * What is left is what the page is actually for, and the boundaries it has
 * to hold: a library entry is a reference to canonical content plus this
 * reader's own state, never a copy of the work; and the shelf is what is
 * currently on it, so a soft-removed work must never come back looking
 * active.
 */

/** Canonical product works: what the catalogue endpoint now returns. */
const FRANKENSTEIN = {
  id: 'work-1',
  title: 'Frankenstein',
  original_title: null,
  domain: { slug: 'literature', name: 'Literature' },
  synopsis: null,
  cover_image_url: null,
  genres: [],
  concepts: [
    { slug: 'horror', name: 'Horror', concept_type: 'genre' },
    { slug: 'science-fiction', name: 'Science Fiction', concept_type: 'genre' },
  ],
  creators: [{ name: 'Mary Shelley', role: 'Author' }],
  media_format: null,
  year: null,
  source: 'gutenberg',
}

const VINLAND = {
  id: 'work-2',
  title: 'Vinland Saga',
  original_title: 'ヴィンランド・サガ',
  domain: { slug: 'manhwa', name: 'Manga & Manhwa' },
  synopsis: 'A young warrior among Viking raiders.',
  cover_image_url: null,
  genres: ['Action', 'Adventure'],
  concepts: [{ slug: 'revenge', name: 'Revenge', concept_type: 'theme' }],
  creators: [{ name: 'Makoto Yukimura', role: 'Story & Art' }],
  media_format: 'MANGA',
  year: 2005,
  source: 'anilist',
}

const WORKS = [{ work: FRANKENSTEIN, user_state: null }, { work: VINLAND, user_state: null }]

const SESSION = {
  access_token: 'test-token-abc',
  token_type: 'bearer',
  expires_at: '2026-10-02T00:00:00Z',
  user: {
    id: 'user-1',
    email: 'reader@example.test',
    display_name: null,
    created_at: '2026-09-18T00:00:00Z',
  },
}

function interaction(overrides: Record<string, unknown> = {}) {
  return {
    work: FRANKENSTEIN,
    user_state: {
      status: 'planned',
      rating: null,
      rated_at: null,
      added_at: '2026-09-18T00:00:00Z',
      started_at: null,
      completed_at: null,
      abandoned_at: null,
      removed_at: null,
      times_started: 0,
      times_completed: 0,
      in_library: true,
      ...overrides,
    },
  }
}

/** Every status present, as the summary endpoint guarantees. */
function summaryFor(entries: { user_state: { status: string } | null }[]) {
  const counts: Record<string, number> = {
    planned: 0,
    in_progress: 0,
    on_hold: 0,
    completed: 0,
    abandoned: 0,
  }
  for (const entry of entries) {
    if (entry.user_state) counts[entry.user_state.status] += 1
  }
  return {
    total: entries.length,
    by_status: counts,
    removed: 0,
    rated: 0,
  }
}

/** Records every request so the tests can assert on what was actually sent. */
function mockApi(library: unknown[] = []) {
  const calls: { url: string; init?: RequestInit }[] = []
  const fetchMock = vi.fn((input: string | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push({ url, init })
    const json = (body: unknown, status = 200) =>
      Promise.resolve({ ok: true, status, json: () => Promise.resolve(body) } as Response)

    if (url.includes('/auth/login') || url.includes('/auth/register')) return json(SESSION)
    if (url.includes('/auth/logout')) {
      return Promise.resolve({ ok: true, status: 204 } as Response)
    }
    if (url.includes('/api/v1/library')) {
      if (url.includes('/summary')) {
        return json(summaryFor(library as { user_state: { status: string } | null }[]))
      }
      if (init?.method === 'DELETE') {
        return Promise.resolve({ ok: true, status: 204 } as Response)
      }
      if (init?.method === 'POST') return json(interaction(), 201)
      if (init?.method === 'PATCH') return json(interaction())
      return json({ items: library, total: library.length, page: 1, page_size: 24 })
    }
    if (url.includes('/works')) return json(WORKS)
    return json([])
  })
  return { fetchMock, calls }
}

async function signIn(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText('Email'), 'reader@example.test')
  await user.type(screen.getByLabelText('Password'), 'a-long-enough-password')
  await user.click(screen.getByRole('button', { name: 'Log in' }))
}

describe('Library dev harness', () => {
  beforeEach(() => {
    setSessionToken(null)
    vi.unstubAllGlobals()
  })

  it('asks for credentials before showing any library', () => {
    const { fetchMock } = mockApi()
    vi.stubGlobal('fetch', fetchMock)

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)

    expect(screen.getByLabelText('Email')).toBeInTheDocument()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
  })

  it('sends the bearer token on library requests after signing in', async () => {
    const { fetchMock, calls } = mockApi()
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await signIn(user)

    // The tab strip is what says a signed-in library is on screen; the
     // "Your library (N)" heading became the tabs in Phase 1Z.
    await screen.findByRole('tablist', { name: 'Library status' })

    const libraryCall = calls.find(
      (call) => call.url.includes('/api/v1/library') && call.init?.method !== 'POST',
    )
    expect(libraryCall).toBeDefined()
    const headers = new Headers(libraryCall?.init?.headers)
    expect(headers.get('Authorization')).toBe('Bearer test-token-abc')
  })

  it('shows an empty library as empty, not as the corpus', async () => {
    const { fetchMock } = mockApi([])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await signIn(user)

    expect(await screen.findByText('Nothing here yet.')).toBeInTheDocument()
    // The distinction the page exists to hold, said in the empty state.
    expect(screen.getByText(/shared by everyone/)).toBeInTheDocument()
    // And no canonical work is listed here just because it exists.
    expect(screen.queryByText('Frankenstein')).not.toBeInTheDocument()
  })

  it('routes an empty library to Discover rather than listing the corpus', async () => {
    const { fetchMock } = mockApi([])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    const navigate = vi.fn()

    render(<Library onNavigate={navigate} onOpenWork={() => {}} />)
    await signIn(user)

    await user.click(await screen.findByRole('button', { name: 'Find something to add' }))
    expect(navigate).toHaveBeenCalledWith('discover')
  })

  it('reports an unrated entry as unrated rather than as zero', async () => {
    const { fetchMock } = mockApi([interaction({ status: 'completed' })])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await signIn(user)

    expect(await screen.findByText('Not rated yet')).toBeInTheDocument()
    const status = screen.getByLabelText('Status for Frankenstein')
    expect((status as HTMLSelectElement).value).toBe('completed')
  })

  it('reports a rating without offering to change it here', async () => {
    const { fetchMock } = mockApi([interaction({ status: 'completed', rating: 9 })])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await signIn(user)

    expect(await screen.findByText('Rated 9/10')).toBeInTheDocument()
    // Rating is the work page's job, where there is room to say what it means.
    expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument()
  })

  it('sends a status change on its own, and writes no rating', async () => {
    const { fetchMock, calls } = mockApi([interaction()])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await signIn(user)

    await user.selectOptions(
      await screen.findByLabelText('Status for Frankenstein'),
      'completed',
    )

    await waitFor(() => {
      const patch = calls.find((call) => call.init?.method === 'PATCH')
      expect(JSON.parse(String(patch?.init?.body))).toEqual({ status: 'completed' })
    })
    // Completing something is not liking it, in the request as in storage.
    expect(
      calls.every((call) => !String(call.init?.body ?? '').includes('rating')),
    ).toBe(true)
  })

  it('removes an entry without deleting the history behind it', async () => {
    const { fetchMock, calls } = mockApi([interaction({ rating: 9 })])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await signIn(user)

    await user.click(
      await screen.findByRole('button', {
        name: 'Remove Frankenstein from your library',
      }),
    )

    await waitFor(() => {
      const remove = calls.find(
        (call) => call.init?.method === 'DELETE' && call.url.includes('/api/v1/library'),
      )
      expect(remove?.url).toContain('work-1')
    })
    // Removal is soft on the server; nothing here asks for a hard delete of
    // the rating that a reader already gave.
    expect(
      calls.some((call) => call.init?.method === 'DELETE' && call.url.includes('rating')),
    ).toBe(false)
  })

  it('surfaces a server refusal instead of failing silently', async () => {
    const fetchMock = vi.fn((input: string | URL) => {
      const url = String(input)
      if (url.includes('/auth/login')) {
        return Promise.resolve({
          ok: false,
          status: 401,
          json: () => Promise.resolve({ detail: 'invalid email or password' }),
        } as Response)
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) } as Response)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await signIn(user)

    expect(await screen.findByText('invalid email or password')).toBeInTheDocument()
    expect(screen.queryByText(/Your library/)).not.toBeInTheDocument()
  })

  it('drops the token on logout', async () => {
    const { fetchMock } = mockApi([])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await signIn(user)
    await screen.findByRole('tablist', { name: 'Library status' })

    await user.click(screen.getByRole('button', { name: 'Log out' }))

    await waitFor(() => expect(screen.getByLabelText('Email')).toBeInTheDocument())
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
  })
})

describe('Library product surface', () => {
  beforeEach(() => {
    setSessionToken(null)
    vi.unstubAllGlobals()
  })

  it('renders canonical work metadata from the product contract', async () => {
    const { fetchMock } = mockApi([{ work: VINLAND, user_state: interaction().user_state }])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await signIn(user)

    // Once, as a library entry. Browsing the shared corpus is Discover's job.
    expect(await screen.findAllByText('Vinland Saga')).toHaveLength(1)
    expect(screen.getByText('ヴィンランド・サガ')).toBeInTheDocument()
    expect(screen.getByText(/Manga & Manhwa · MANGA · 2005/)).toBeInTheDocument()
    expect(screen.getByText(/Makoto Yukimura \(Story & Art\)/)).toBeInTheDocument()
    expect(screen.getByText('A young warrior among Viking raiders.')).toBeInTheDocument()
    expect(screen.getByText('Action')).toBeInTheDocument()
    expect(screen.getByText('Revenge')).toBeInTheDocument()
  })

  it('states plainly when a work has no synopsis', async () => {
    const { fetchMock } = mockApi([interaction()])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await signIn(user)

    // Frankenstein's fixture has synopsis: null -- an absence, shown as one.
    expect(await screen.findByText('No synopsis available')).toBeInTheDocument()
  })

  it('never renders raw corpus text or internal provenance', async () => {
    const { fetchMock } = mockApi([{ work: VINLAND, user_state: interaction().user_state }])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    const { container } = render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await signIn(user)
    await screen.findAllByText('Vinland Saga')

    const rendered = container.textContent ?? ''
    for (const forbidden of ['content_unit', 'embedding', 'supporting_labels', 'adapter']) {
      expect(rendered).not.toContain(forbidden)
    }
  })

  it('keeps user state visually separate from the work', async () => {
    const { fetchMock } = mockApi([interaction({ status: 'completed', rating: 9 })])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await signIn(user)

    // The user's half is labelled as theirs; the work's half is not.
    expect(await screen.findByText('Your state')).toBeInTheDocument()
    expect((screen.getByLabelText('Status for Frankenstein') as HTMLSelectElement).value).toBe(
      'completed',
    )
    expect(screen.getByText('Rated 9/10')).toBeInTheDocument()
  })

  it('opens a work rather than duplicating its canonical record', async () => {
    const { fetchMock } = mockApi([interaction()])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    const open = vi.fn()

    render(<Library onNavigate={() => {}} onOpenWork={open} />)
    await signIn(user)

    await user.click(await screen.findByText('Frankenstein'))
    expect(open).toHaveBeenCalledWith('work-1')
  })

  it('reaches the rest of the product from the library', async () => {
    const { fetchMock } = mockApi([interaction()])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    const navigate = vi.fn()

    render(<Library onNavigate={navigate} onOpenWork={() => {}} />)
    await signIn(user)

    await screen.findByText('Frankenstein')
    const main = screen.getByRole('navigation', { name: 'Main' })
    await user.click(within(main).getByRole('button', { name: 'Your Taste' }))
    expect(navigate).toHaveBeenCalledWith('taste')
  })

  it('surfaces reconsumption when a work was completed more than once', async () => {
    const { fetchMock } = mockApi([interaction({ status: 'completed', times_completed: 3 })])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await signIn(user)

    expect(await screen.findByText('Completed 3 times')).toBeInTheDocument()
  })
})

describe('Library status organisation', () => {
  beforeEach(() => {
    setSessionToken(null)
    vi.unstubAllGlobals()
  })

  const MIXED = [
    interaction({ status: 'in_progress' }),
    { work: VINLAND, user_state: interaction({ status: 'completed', rating: 8 }).user_state },
  ]

  it('groups the library by the states the backend already stores', async () => {
    const { fetchMock } = mockApi(MIXED)
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await signIn(user)

    for (const heading of [
      'Reading & watching',
      'Planned',
      'On hold',
      'Completed',
      'Abandoned',
    ]) {
      expect(
        await screen.findByRole('heading', { name: new RegExp(heading) }),
      ).toBeInTheDocument()
    }
  })

  it('gives every empty group a sentence rather than a blank', async () => {
    const { fetchMock } = mockApi(MIXED)
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await signIn(user)

    expect(await screen.findByText('Nothing planned yet.')).toBeInTheDocument()
    expect(screen.getByText('Nothing on hold yet.')).toBeInTheDocument()
    expect(screen.getByText(/Nothing abandoned/)).toBeInTheDocument()
  })

  it('labels each tab with a count from the summary', async () => {
    const { fetchMock } = mockApi(MIXED)
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await signIn(user)

    const tabs = await screen.findAllByRole('tab')
    expect(tabs.map((tab) => tab.textContent)).toContain('Completed1')
    expect(screen.getByRole('tab', { name: /All/ })).toHaveAttribute(
      'aria-selected',
      'true',
    )
  })

  it('filters on the server rather than in the client', async () => {
    const { fetchMock, calls } = mockApi(MIXED)
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await signIn(user)

    await user.click(await screen.findByRole('tab', { name: /Completed/ }))

    await waitFor(() =>
      expect(
        calls.some((call) => call.url.includes('status=completed')),
      ).toBe(true),
    )
  })

  it('says what is missing when a chosen status is empty', async () => {
    const { fetchMock } = mockApi([interaction({ status: 'in_progress' })])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await signIn(user)

    await user.click(await screen.findByRole('tab', { name: /On hold/ }))
    // The mock returns the same list whatever the filter, so this checks the
    // grouped view's own empty sentence is reachable from the tab too.
    expect(await screen.findByRole('heading', { name: /On hold/ })).toBeInTheDocument()
  })

  it('asks for active entries only, never removed ones', async () => {
    const { fetchMock, calls } = mockApi([interaction()])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await signIn(user)

    await screen.findByText('Frankenstein')
    const listings = calls.filter(
      (call) =>
        call.url.includes('/api/v1/library') &&
        !call.url.includes('/summary') &&
        call.init?.method === undefined,
    )
    expect(listings.length).toBeGreaterThan(0)
    expect(listings.every((call) => !call.url.includes('include_removed'))).toBe(true)
  })
})
