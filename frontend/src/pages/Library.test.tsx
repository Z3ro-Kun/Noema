import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Library from './Library'
import { setSessionToken } from '../api/client'
import { resetSessionForTests, signIn } from '../auth/session'
import { getSessionToken } from '../api/client'

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

const REMOVED_AT = '2026-09-19T10:00:00Z'

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

type Entry = { user_state: { status: string; in_library?: boolean } | null }

/** Held entries only, the way the server's default listing filters. */
function held(entries: Entry[]): Entry[] {
  return entries.filter((entry) => entry.user_state?.in_library !== false)
}

/** Every status present, as the summary endpoint guarantees. */
function summaryFor(entries: Entry[]) {
  const counts: Record<string, number> = {
    planned: 0,
    in_progress: 0,
    on_hold: 0,
    completed: 0,
    abandoned: 0,
  }
  for (const entry of held(entries)) {
    if (entry.user_state) counts[entry.user_state.status] += 1
  }
  return {
    total: held(entries).length,
    by_status: counts,
    removed: entries.length - held(entries).length,
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
      // Filtered the way the server filters, so a client that asks for one
      // status cannot be handed the whole library and look correct.
      const params = new URL(url, 'http://localhost').searchParams
      const status = params.get('status')
      const domain = params.get('domain')
      const includeRemoved = params.get('include_removed') === 'true'
      let items = library as Entry[]
      if (!includeRemoved) items = held(items)
      if (status) items = items.filter((entry) => entry.user_state?.status === status)
      // The server filters on both; a client that filtered on neither would
      // otherwise look correct here.
      if (domain) {
        items = items.filter(
          (entry) => (entry as { work?: { domain_slug?: string } }).work?.domain_slug === domain,
        )
      }
      const size = Number(params.get('page_size') ?? 24)
      return json({
        items: items.slice(0, size),
        total: items.length,
        page: Number(params.get('page') ?? 1),
        page_size: size,
      })
    }
    if (url.includes('/works')) return json(WORKS)
    return json([])
  })
  return { fetchMock, calls }
}

/**
 * Authenticate through the shared session store.
 *
 * The login form no longer lives on this page -- there is one Login page now
 * -- so a test signs in the way the application does: by driving the store
 * every component observes.
 */
async function authenticate(_user?: ReturnType<typeof userEvent.setup>) {
  await act(async () => {
    await signIn('reader@example.test', 'a-long-enough-password')
  })
}

describe('Library dev harness', () => {
  beforeEach(() => {
    resetSessionForTests()
    setSessionToken(null)
    vi.unstubAllGlobals()
  })

  it('asks for credentials before showing any library', () => {
    const { fetchMock } = mockApi()
    vi.stubGlobal('fetch', fetchMock)

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)

    // A prompt pointing at the Login page, not a second login form.
    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Log in' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Email')).not.toBeInTheDocument()
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
  })

  it('sends an anonymous reader to the Login page', async () => {
    const user = userEvent.setup()
    const { fetchMock } = mockApi()
    vi.stubGlobal('fetch', fetchMock)
    const navigate = vi.fn()

    render(<Library onNavigate={navigate} onOpenWork={() => {}} />)
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    expect(navigate).toHaveBeenCalledWith('login')
  })

  it('sends the bearer token on library requests after signing in', async () => {
    const { fetchMock, calls } = mockApi()
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)

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
    await authenticate(user)

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
    await authenticate(user)

    await user.click(await screen.findByRole('button', { name: 'Find something to add' }))
    expect(navigate).toHaveBeenCalledWith('discover')
  })

  it('reports an unrated entry as unrated rather than as zero', async () => {
    const { fetchMock } = mockApi([interaction({ status: 'completed' })])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)

    // Stated as an absence, in words. Unrated is not a low score and must
    // never be rendered as one -- which in a ruled ledger also means never as
    // a dash in the numeric column.
    expect(await screen.findByText(/Not rated/)).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/rated 0\/10/)
    expect(document.body.textContent).not.toMatch(/0 \/ 10/)
    const status = screen.getByLabelText('Status for Frankenstein')
    expect((status as HTMLSelectElement).value).toBe('completed')
  })

  it('reports a rating without offering to change it here', async () => {
    const { fetchMock } = mockApi([interaction({ status: 'completed', rating: 9 })])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)

    expect(await screen.findByText(/rated 9\/10/)).toBeInTheDocument()
    // Rating is the work page's job, where there is room to say what it means.
    expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/Rate /)).not.toBeInTheDocument()
  })

  it('sends a status change on its own, and writes no rating', async () => {
    const { fetchMock, calls } = mockApi([interaction()])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)

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
    await authenticate(user)

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

  it('surfaces a refused library read instead of failing silently', async () => {
    // Login failures belong to the Login page now; what this page must not
    // swallow is a refusal on its own request.
    const fetchMock = vi.fn((input: string | URL) => {
      const url = String(input)
      if (url.includes('/auth/login')) {
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(SESSION),
        } as Response)
      }
      if (url.includes('/api/v1/library')) {
        return Promise.resolve({
          ok: false,
          status: 503,
          json: () => Promise.resolve({ detail: 'the library is unavailable' }),
        } as Response)
      }
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve([]) } as Response)
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The library is unavailable.',
    )
  })

  it('drops the token on logout', async () => {
    const { fetchMock } = mockApi([])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)
    await screen.findByRole('tablist', { name: 'Library status' })

    await user.click(screen.getByRole('button', { name: 'Log out' }))

    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument(),
    )
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument()
    // The shared store is anonymous again, and the token is gone with it.
    expect(getSessionToken()).toBeNull()
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
    await authenticate(user)

    // Once, as a library entry. Browsing the shared corpus is Discover's job.
    expect(await screen.findAllByText('Vinland Saga')).toHaveLength(1)
    expect(screen.getByText('ヴィンランド・サガ')).toBeInTheDocument()
    // Creator, medium, format and year: enough to know which work this is,
    // which is all a library row has to do. The ledger distributes them
    // across its columns rather than joining them into one caption, so each
    // is asserted where it now lives.
    expect(screen.getByText('Makoto Yukimura')).toBeInTheDocument()
    // The medium filter offers the same words, so the assertion is that the
    // row states it -- not merely that the page contains it somewhere.
    expect(
      screen.getAllByText('Manga & Manhwa').some((node) => node.tagName !== 'OPTION'),
    ).toBe(true)
    expect(screen.getByText(/MANGA · 2005/)).toBeInTheDocument()
  })

  it('leaves synopsis and label chips to the work page', async () => {
    const { fetchMock } = mockApi([
      { work: VINLAND, user_state: interaction().user_state },
    ])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)

    // A deliberate reduction: a library row says which work it is and what
    // the reader did with it. Describing the work is the work page's job,
    // and it still states an absent synopsis as an absence there.
    await screen.findAllByText('Vinland Saga')
    expect(
      screen.queryByText('A young warrior among Viking raiders.'),
    ).not.toBeInTheDocument()
    expect(screen.queryByText('Action')).not.toBeInTheDocument()
    expect(screen.queryByText('Revenge')).not.toBeInTheDocument()
  })

  it('never renders raw corpus text or internal provenance', async () => {
    const { fetchMock } = mockApi([{ work: VINLAND, user_state: interaction().user_state }])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    const { container } = render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)
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
    await authenticate(user)

    // The user's half is labelled as theirs; the work's half is not. Twice
    // in the DOM and once on screen: the ledger's column head labels it from
    // the medium breakpoint up, the row labels its own cell below that.
    expect((await screen.findAllByText('Your relationship')).length).toBeGreaterThan(0)
    expect((screen.getByLabelText('Status for Frankenstein') as HTMLSelectElement).value).toBe(
      'completed',
    )
    expect(screen.getByText(/rated 9\/10/)).toBeInTheDocument()
  })

  it('opens a work rather than duplicating its canonical record', async () => {
    const { fetchMock } = mockApi([interaction()])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    const open = vi.fn()

    render(<Library onNavigate={() => {}} onOpenWork={open} />)
    await authenticate(user)

    await user.click(await screen.findByText('Frankenstein'))
    expect(open).toHaveBeenCalledWith('work-1')
  })

  it('reaches the rest of the product from the library', async () => {
    const { fetchMock } = mockApi([interaction()])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()
    const navigate = vi.fn()

    render(<Library onNavigate={navigate} onOpenWork={() => {}} />)
    await authenticate(user)

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
    await authenticate(user)

    // Said in the medium's own verb, and as the count rather than the word.
    expect(await screen.findByText('Read 3 times')).toBeInTheDocument()
  })
})

describe('Library status organisation', () => {
  beforeEach(() => {
    // The store as well as the token: leaving a previous test's `account`
    // behind makes the page mount authenticated with no token and fetch
    // before this test has signed in.
    resetSessionForTests()
    setSessionToken(null)
    vi.unstubAllGlobals()
  })

  const MIXED = [
    interaction({ status: 'in_progress' }),
    { work: VINLAND, user_state: interaction({ status: 'completed', rating: 8 }).user_state },
  ]

  it('offers every state the backend stores as a way to narrow the ledger', async () => {
    // The archive used to render a headed block per status, which meant a
    // reader whose library was all completed scrolled past four large empty
    // blocks to reach it. It is one ruled register now and the statuses are
    // the tabs that filter it -- so what must hold is that every state the
    // backend stores is still offered, and that the ledger under them holds
    // the rows.
    const { fetchMock } = mockApi(MIXED)
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)

    for (const label of [
      'All',
      'Reading & watching',
      'Planned',
      'On hold',
      'Completed',
      'Abandoned',
    ]) {
      expect(
        await screen.findByRole('tab', { name: new RegExp(label) }),
      ).toBeInTheDocument()
    }
    expect(await screen.findByText('Frankenstein')).toBeInTheDocument()
  })

  it('reports an empty state as a zero on its tab, not as an empty block', async () => {
    const { fetchMock } = mockApi(MIXED)
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)

    // MIXED holds nothing planned, on hold or abandoned. Each says so where a
    // reader looks for it -- on the tab -- rather than by spending a screen
    // on a headed block with a sentence in it.
    for (const label of ['Planned', 'On hold', 'Abandoned']) {
      const tab = await screen.findByRole('tab', { name: new RegExp(label) })
      expect(tab.textContent).toMatch(/0$/)
    }
  })

  it('labels each tab with a count from the summary', async () => {
    const { fetchMock } = mockApi(MIXED)
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)

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
    await authenticate(user)

    await user.click(await screen.findByRole('tab', { name: /Completed/ }))

    await waitFor(() =>
      expect(
        calls.some((call) => call.url.includes('status=completed')),
      ).toBe(true),
    )
  })

  it('keeps the previous rows up while a tab switch is in flight', async () => {
    // `loading` used to suppress the content while the "nothing yet" message
    // was suppressed by having content, so a switch fell between the two and
    // showed an empty page.
    let release: (() => void) | undefined
    const gate = new Promise<void>((resolve) => {
      release = resolve
    })
    // The grouped view asks for every status, completed included, so the
    // gate is only armed once the first screen is up.
    let armed = false

    const fetchMock = vi.fn(async (input: string | URL) => {
      const url = String(input)
      const json = (body: unknown) =>
        ({ ok: true, status: 200, json: () => Promise.resolve(body) }) as Response

      if (url.includes('/auth/login')) return json(SESSION)
      if (url.includes('/summary')) return json(summaryFor(MIXED))
      if (url.includes('/api/v1/library')) {
        const status = new URL(url, 'http://localhost').searchParams.get('status')
        // Hold the tab the reader is switching to, and nothing else.
        if (armed && status === 'completed') await gate
        const items = status
          ? MIXED.filter((entry) => entry.user_state?.status === status)
          : MIXED
        return json({ items, total: items.length, page: 1, page_size: 24 })
      }
      return json([])
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)
    // Anchored on a row rather than on a group head: the ledger has no group
    // heads any more, and the row is what a reader would actually notice
    // disappearing.
    await screen.findByText('Frankenstein')

    armed = true
    await user.click(screen.getByRole('tab', { name: /Completed/ }))

    // Mid-flight: the previous rows are still on screen, and the page says
    // what it is doing rather than going blank.
    expect(screen.getByText('Frankenstein')).toBeInTheDocument()
    expect(await screen.findByText('Updating…')).toBeInTheDocument()

    release?.()
    await waitFor(() => expect(screen.queryByText('Updating…')).not.toBeInTheDocument())
    expect(screen.getByRole('tab', { name: /Completed/ })).toHaveAttribute(
      'aria-selected',
      'true',
    )
  })

  it('says what is missing when a chosen status is empty', async () => {
    const { fetchMock } = mockApi([interaction({ status: 'in_progress' })])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)

    await user.click(await screen.findByRole('tab', { name: /On hold/ }))
    // The mock returns the same list whatever the filter, so this checks the
    // grouped view's own empty sentence is reachable from the tab too.
    expect(await screen.findByRole('heading', { name: /On hold/ })).toBeInTheDocument()
  })

  it('never renders controls on a removed entry that the server refuses', async () => {
    // `set_status` and `remove_from_library` both raise on an interaction
    // whose `removed_at` is set, so the API answers 404. Offering those
    // controls on a removed row is offering a guaranteed failure.
    const { fetchMock } = mockApi([
      interaction({ status: 'completed', rating: 9, in_library: false, removed_at: REMOVED_AT }),
    ])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)
    await user.click(await screen.findByRole('button', { name: 'Show them' }))

    expect(
      await screen.findByRole('heading', { name: 'Removed' }),
    ).toBeInTheDocument()
    expect(screen.queryByLabelText('Status for Frankenstein')).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Remove Frankenstein from your library' }),
    ).not.toBeInTheDocument()
  })

  it('offers the one path that does work: adding a removed entry back', async () => {
    const { fetchMock, calls } = mockApi([
      interaction({ status: 'completed', rating: 9, in_library: false, removed_at: REMOVED_AT }),
    ])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)
    await user.click(await screen.findByRole('button', { name: 'Show them' }))

    await user.click(
      await screen.findByRole('button', {
        name: 'Add Frankenstein back to your library',
      }),
    )

    await waitFor(() => {
      const posted = calls.find(
        (call) =>
          call.init?.method === 'POST' && call.url.endsWith('/api/v1/library'),
      )
      // The add endpoint revives the existing row; nothing here asks for a
      // second interaction or tries to restore the rating by hand.
      expect(JSON.parse(String(posted?.init?.body))).toEqual({ work_id: 'work-1' })
    })
    expect(
      calls.every((call) => !String(call.init?.body ?? '').includes('rating')),
    ).toBe(true)
  })

  it('says a removed entry is removed rather than showing it as held', async () => {
    const { fetchMock } = mockApi([
      interaction({ status: 'completed', rating: 9, in_library: false, removed_at: REMOVED_AT }),
      { work: VINLAND, user_state: interaction({ status: 'completed' }).user_state },
    ])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)
    await user.click(await screen.findByRole('button', { name: 'Show them' }))

    // It is named as removed, and it is kept in its own section so it never
    // sits in the ledger beside works that are still on the shelf.
    const removed = (
      await screen.findByRole('heading', { name: 'Removed' })
    ).closest('section') as HTMLElement
    expect(within(removed).getByText('Frankenstein')).toBeInTheDocument()
    expect(within(removed).queryByText('Vinland Saga')).not.toBeInTheDocument()
  })

  it('cannot show a group count that disagrees with the rows under it', async () => {
    // Nine completed works, six shown. The count is the server's total for
    // that status and the rows are a prefix of it, so the heading is never a
    // description of what is on screen unless it says so.
    const many = Array.from({ length: 9 }, (_, index) => ({
      work: { ...FRANKENSTEIN, id: `work-${index}` },
      user_state: interaction({ status: 'completed' }).user_state,
    }))
    const { fetchMock } = mockApi(many)
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)

    await screen.findAllByText('Frankenstein')
    // The tab carries the server's total for the status; the ledger carries a
    // prefix of it. The shortfall is stated outright rather than left to be
    // inferred from a count that disagrees with the rows beneath it.
    expect(screen.getByRole('tab', { name: /Completed/ }).textContent).toMatch(/9$/)
    // Counted by the per-row status control rather than by <li>, which the
    // tab strip also uses.
    expect(screen.getAllByLabelText(/^Status for /).length).toBe(6)
    expect(screen.getByText(/Showing 6 of 9/)).toBeInTheDocument()
  })

  it('asks the server for each group rather than splitting one page', async () => {
    const { fetchMock, calls } = mockApi([
      interaction({ status: 'in_progress' }),
      { work: VINLAND, user_state: interaction({ status: 'completed' }).user_state },
    ])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)
    await screen.findAllByText('Frankenstein')

    // One request per status in the vocabulary -- a constant, not an N+1.
    for (const status of ['in_progress', 'planned', 'on_hold', 'completed', 'abandoned']) {
      expect(calls.some((call) => call.url.includes(`status=${status}`))).toBe(true)
    }
  })

  it('keeps one library addressed by token alone', async () => {
    // The listing is addressed by bearer token alone; no request this page
    // makes names a user, so there is no parameter to point elsewhere.
    const { fetchMock, calls } = mockApi([interaction()])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)
    await screen.findByText('Frankenstein')

    const library = calls.filter((call) => call.url.includes('/api/v1/library'))
    expect(library.length).toBeGreaterThan(0)
    for (const call of library) {
      expect(call.url).not.toMatch(/user_id|user=|account/)
      expect(String(call.init?.body ?? '')).not.toMatch(/user_id/)
      // Read from the entries rather than `.get`: this environment's
      // `Headers` is case-sensitive on lookup, though the header is present.
      const sent = call.init?.headers as Headers | undefined
      expect(sent).toBeDefined()
      const headers = Object.fromEntries([...(sent ?? new Headers())])
      expect(headers.authorization).toBe('Bearer test-token-abc')
    }
  })

  // --- narrowing by medium --------------------------------------------------

  it('asks the server to narrow by medium', async () => {
    const user = userEvent.setup()
    const { fetchMock, calls } = mockApi(MIXED)
    vi.stubGlobal('fetch', fetchMock)

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)
    await screen.findAllByText('Frankenstein')

    await user.selectOptions(screen.getByLabelText('Medium'), 'anime')

    await waitFor(() =>
      expect(calls.some((call) => call.url.includes('domain=anime'))).toBe(true),
    )
    // Server-side: nothing downloads the whole library to hide part of it.
    const listings = calls.filter(
      (call) => call.url.includes('/api/v1/library') && !call.url.includes('/summary'),
    )
    expect(listings.length).toBeGreaterThan(0)
  })

  it('combines medium with status in one request', async () => {
    const user = userEvent.setup()
    const { fetchMock, calls } = mockApi(MIXED)
    vi.stubGlobal('fetch', fetchMock)

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)
    await screen.findAllByText('Frankenstein')

    await user.selectOptions(screen.getByLabelText('Medium'), 'literature')
    await user.click(await screen.findByRole('tab', { name: /Completed/ }))

    await waitFor(() => {
      const combined = calls.filter(
        (call) =>
          call.url.includes('status=completed') && call.url.includes('domain=literature'),
      )
      expect(combined.length).toBeGreaterThan(0)
    })
  })

  it('returns to the first page when the medium changes', async () => {
    const user = userEvent.setup()
    const many = Array.from({ length: 30 }, (_, index) => ({
      work: { ...FRANKENSTEIN, id: `work-${index}` },
      user_state: interaction({ status: 'completed' }).user_state,
    }))
    const { fetchMock, calls } = mockApi(many)
    vi.stubGlobal('fetch', fetchMock)

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)
    await user.click(await screen.findByRole('tab', { name: /Completed/ }))
    await screen.findAllByText('Frankenstein')
    await user.click(await screen.findByRole('button', { name: 'Next' }))
    await waitFor(() => expect(calls.some((call) => call.url.includes('page=2'))).toBe(true))

    await user.selectOptions(screen.getByLabelText('Medium'), 'anime')

    // Page three of the old filter is not a page of the new one.
    await waitFor(() => {
      const latest = [...calls]
        .reverse()
        .find((call) => call.url.includes('domain=anime'))
      expect(latest?.url).not.toContain('page=2')
    })
  })

  it('offers a way back to every medium once narrowed', async () => {
    const user = userEvent.setup()
    const { fetchMock, calls } = mockApi(MIXED)
    vi.stubGlobal('fetch', fetchMock)

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)
    await screen.findAllByText('Frankenstein')

    await user.selectOptions(screen.getByLabelText('Medium'), 'anime')
    await user.click(await screen.findByRole('button', { name: 'Show all media' }))

    await waitFor(() => {
      const latest = [...calls]
        .reverse()
        .find((call) => call.url.includes('/api/v1/library') && !call.url.includes('/summary'))
      expect(latest?.url).not.toContain('domain=')
    })
  })

  it('sends no medium at all until one is chosen', async () => {
    const { fetchMock, calls } = mockApi(MIXED)
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)
    await screen.findAllByText('Frankenstein')

    expect(calls.every((call) => !call.url.includes('domain='))).toBe(true)
  })

  it('asks for active entries only, never removed ones', async () => {
    const { fetchMock, calls } = mockApi([interaction()])
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<Library onNavigate={() => {}} onOpenWork={() => {}} />)
    await authenticate(user)

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
