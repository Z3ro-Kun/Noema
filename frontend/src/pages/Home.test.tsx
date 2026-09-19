import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Home from './Home'
import { setSessionToken } from '../api/client'
import { resetSessionForTests, setAuthenticatedForTests } from '../auth/session'
import {
  ACCOUNT,
  ANIME,
  SESSION,
  WORK,
  libraryPage,
  librarySummary,
  listPage,
  presentation,
  userState,
} from '../test/fixtures'
import type { TasteDashboard } from '../types/api'

/**
 * Home.
 *
 * Two pages really, and they answer different questions. Signed out: what is
 * Noema and what is in it. Signed in: what was I doing, what has Noema
 * noticed, where do I go next.
 *
 * The load-bearing assertions are about what Home is *not*:
 *
 *   it does not require an account to browse shared canonical content;
 *
 *   its "Because you enjoy X" shelves are a theme filter and say so -- a
 *   reader can reproduce them exactly in Discover, which is the test of
 *   whether a personalised-looking shelf is honest;
 *
 *   it says "keep rating" rather than inventing a preference when there is
 *   not enough evidence for one.
 */

const EMPTY_DASHBOARD: TasteDashboard = {
  summary: {
    profile_state: 'no_activity',
    rated_works: 0,
    established_preferences: 0,
    emerging_signals: 0,
  },
  strongly_likes: [],
  mildly_likes: [],
  dislikes: [],
  emerging: [],
  what_stands_out: [],
}

function withPreference(name: string, slug: string): TasteDashboard {
  return {
    ...EMPTY_DASHBOARD,
    summary: { ...EMPTY_DASHBOARD.summary, profile_state: 'established', rated_works: 6 },
    strongly_likes: [
      {
        key: slug,
        display_name: name,
        features: [{ key: slug, name }],
        kind: 'individual',
        direction: 'positive',
        confidence_band: 'moderate',
        presentation_key: 'enjoys_feature',
        domains: ['Anime'],
        evidence_summary: {
          rated_works: 5,
          supporting_works: 6,
          domains: ['Anime'],
          includes_reconsumed_works: false,
          has_mixed_evidence: false,
        },
        also_supported_by: [],
      },
    ],
  }
}

interface Options {
  signedIn?: boolean
  library?: unknown[]
  dashboard?: TasteDashboard | null
  dashboardFails?: boolean
  worksFail?: boolean
}

let requests: string[] = []

function mockApi(options: Options = {}) {
  requests = []
  return vi.fn((input: string | URL, init?: RequestInit) => {
    const url = String(input)
    requests.push(url)
    const json = (body: unknown) =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) } as Response)
    const fail = (status: number, detail: string) =>
      Promise.resolve({ ok: false, status, json: () => Promise.resolve({ detail }) } as Response)

    if (url.includes('/auth/me')) {
      return options.signedIn ? json(ACCOUNT) : fail(401, 'not authenticated')
    }
    if (url.includes('/auth/login') || url.includes('/auth/register')) return json(SESSION)
    if (url.includes('/auth/logout')) {
      return Promise.resolve({ ok: true, status: 204 } as Response)
    }
    if (url.includes('/preferences/dashboard')) {
      if (options.dashboardFails) return fail(503, 'unavailable')
      return json(options.dashboard ?? EMPTY_DASHBOARD)
    }
    if (url.includes('/api/v1/library') && init?.method === undefined) {
      if (url.includes('/summary')) return json(librarySummary())
      return json(libraryPage((options.library ?? []) as never[]))
    }
    if (url.includes('/api/v1/works')) {
      if (options.worksFail) return fail(503, 'the catalogue is unavailable')
      return json(listPage([presentation(WORK), presentation(ANIME)]))
    }
    return Promise.reject(new Error(`unexpected request: ${url}`))
  })
}

function renderPage(options: Options = {}, props: Record<string, unknown> = {}) {
  if (options.signedIn) setAuthenticatedForTests(ACCOUNT.email)
  vi.stubGlobal('fetch', mockApi(options))
  return render(
    <Home
      onNavigate={() => {}}
      onOpenWork={() => {}}
      onExplore={() => {}}
      {...props}
    />,
  )
}

describe('Home, signed out', () => {
  beforeEach(() => {
    resetSessionForTests()
    setSessionToken(null)
    vi.unstubAllGlobals()
  })

  it('explains what Noema is without demanding an account', async () => {
    renderPage()

    expect(await screen.findByRole('heading', { level: 1, name: 'Noema' })).toBeInTheDocument()
    expect(
      screen.getByText(/reads across literature, anime and manga as one collection/),
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.getByText(/Browsing is open to everyone/)).toBeInTheDocument()
  })

  it('introduces Noema rather than featuring a work', async () => {
    renderPage()

    // The landing is about the product. An earlier pass featured whichever
    // work sorted first, which put a record with no synopsis in the most
    // prominent position on the page.
    expect(
      await screen.findByRole('heading', { name: /Keep what you read and watch in one place/ }),
    ).toBeInTheDocument()
    expect(screen.getByText(/not a rating site and not a recommender/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'How it works' })).toBeInTheDocument()
  })

  it('names the three media it covers', async () => {
    renderPage()

    await screen.findByRole('heading', { level: 1, name: 'Noema' })
    for (const label of ['Literature', 'Anime', 'Manga & Manhwa']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
    }
  })

  it('names no work, and asks the API for nothing', async () => {
    renderPage()

    await screen.findByRole('heading', { level: 1, name: 'Noema' })

    // The catalogue and its detail are the signed-in experience. Nothing on
    // the landing names a work, and no request goes out to fetch one.
    expect(screen.queryByText("Alice's Adventures in Wonderland")).not.toBeInTheDocument()
    expect(screen.queryByText('Cowboy Bebop')).not.toBeInTheDocument()
    expect(requests.filter((url) => url.includes('/api/v1/works'))).toHaveLength(0)
    expect(requests.filter((url) => url.includes('/api/v1/library'))).toHaveLength(0)
    expect(requests.filter((url) => url.includes('/preferences'))).toHaveLength(0)
  })

  it('exposes no private user information', async () => {
    const { container } = renderPage()

    await screen.findByRole('heading', { level: 1, name: 'Noema' })
    const rendered = container.textContent ?? ''
    expect(rendered).not.toContain('reader@example.test')
    expect(rendered).not.toMatch(/Your library|Recent activity|Your taste/)
    // No synopsis, no rating, no library state -- there is no work here at all.
    expect(rendered).not.toMatch(/rated \d+\/10|In your library|No synopsis/)
  })

  it('cannot be broken by a catalogue failure it never calls', async () => {
    renderPage({ worksFail: true })

    // Nothing is fetched, so there is nothing to fail.
    expect(await screen.findByRole('heading', { level: 1, name: 'Noema' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('carries a chosen medium into Discover', async () => {
    const user = userEvent.setup()
    const explore = vi.fn()
    renderPage({}, { onExplore: explore })

    await screen.findByRole('heading', { level: 1, name: 'Noema' })
    await user.click(screen.getByRole('button', { name: 'Anime' }))

    expect(explore).toHaveBeenCalledWith({ domain: 'anime' })
  })

  it('routes to the catalogue without naming anything in it', async () => {
    const user = userEvent.setup()
    const navigate = vi.fn()
    renderPage({}, { onNavigate: navigate })

    await user.click(
      await screen.findByRole('button', { name: 'Browse the catalogue first' }),
    )
    expect(navigate).toHaveBeenCalledWith('discover')
  })

  it('triggers no library action for an anonymous visitor', async () => {
    const user = userEvent.setup()
    renderPage()

    await screen.findByRole('heading', { level: 1, name: 'Noema' })
    // There is no add control at all, and nothing here can write.
    expect(screen.queryByRole('button', { name: /Add to library/ })).not.toBeInTheDocument()

    // Two CTAs point at Register -- the hero and the sign-in block.
    await user.click(screen.getAllByRole('button', { name: 'Create an account' })[0])
    expect(
      requests.filter((url) => url.includes('/api/v1/library')),
    ).toHaveLength(0)
  })
})

describe('Home, signed in', () => {
  beforeEach(() => {
    resetSessionForTests()
    setSessionToken(null)
    vi.unstubAllGlobals()
  })

  it('greets the reader and shows their recent activity', async () => {
    renderPage({
      signedIn: true,
      library: [presentation(ANIME, userState({ status: 'in_progress' }))],
    })

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Welcome back' }),
    ).toBeInTheDocument()
    // The greeting renders as soon as the session resolves; the sections wait
    // on the library request, so this has to be awaited rather than assumed.
    expect(await screen.findByRole('heading', { name: 'Recent activity' })).toBeInTheDocument()
    expect(screen.getByText('Cowboy Bebop')).toBeInTheDocument()
    // Said as news rather than as a status token.
    expect(screen.getByText('Currently watching')).toBeInTheDocument()
  })

  it('tells a reader with an empty library where to start', async () => {
    const user = userEvent.setup()
    const navigate = vi.fn()
    renderPage({ signedIn: true, library: [] }, { onNavigate: navigate })

    expect(await screen.findByText('Nothing in your library yet.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Find something to add' }))
    expect(navigate).toHaveBeenCalledWith('discover')
  })

  it('previews the strongest preference and routes to the full profile', async () => {
    const user = userEvent.setup()
    const navigate = vi.fn()
    renderPage(
      { signedIn: true, dashboard: withPreference('Psychological Depth', 'psychological-depth') },
      { onNavigate: navigate },
    )

    expect(
      await screen.findByText(/You particularly enjoy/),
    ).toBeInTheDocument()
    expect(screen.getByText('Psychological Depth')).toBeInTheDocument()
    expect(screen.getByText(/From 6 rated works/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'View your full taste profile' }))
    expect(navigate).toHaveBeenCalledWith('taste')
  })

  it('says to keep rating rather than inventing a preference', async () => {
    renderPage({ signedIn: true, dashboard: EMPTY_DASHBOARD })

    expect(
      await screen.findByText('Keep rating works to build your taste profile.'),
    ).toBeInTheDocument()
    expect(screen.getByText(/finishing it only says you got to the end/)).toBeInTheDocument()
    expect(screen.queryByText(/You particularly enjoy/)).not.toBeInTheDocument()
  })

  it('still shows the library when the taste profile fails to load', async () => {
    renderPage({
      signedIn: true,
      dashboardFails: true,
      library: [presentation(ANIME, userState())],
    })

    expect(await screen.findByText('Cowboy Bebop')).toBeInTheDocument()
    expect(screen.getByText('Keep rating works to build your taste profile.')).toBeInTheDocument()
  })

  // --- taste-guided shelves ------------------------------------------------

  it('builds a shelf from an established preference, and names the mechanism', async () => {
    renderPage({
      signedIn: true,
      dashboard: withPreference('Psychological Depth', 'psychological-depth'),
    })

    expect(
      await screen.findByRole('heading', { name: 'Because you enjoy Psychological Depth' }),
    ).toBeInTheDocument()
    // A filter, said out loud. Not a model, and reproducible in Discover.
    expect(
      screen.getByText(/the same list anyone gets by filtering Discover on Psychological Depth/i),
    ).toBeInTheDocument()
  })

  it('asks the server for the shelf by concept rather than ranking locally', async () => {
    renderPage({
      signedIn: true,
      dashboard: withPreference('Psychological Depth', 'psychological-depth'),
    })

    await screen.findByRole('heading', { name: 'Because you enjoy Psychological Depth' })
    expect(
      requests.some((url) => url.includes('concept=psychological-depth')),
    ).toBe(true)
  })

  it('hands a shelf’s theme to Discover unchanged', async () => {
    const user = userEvent.setup()
    const explore = vi.fn()
    renderPage(
      { signedIn: true, dashboard: withPreference('Psychological Depth', 'psychological-depth') },
      { onExplore: explore },
    )

    await screen.findByRole('heading', { name: 'Because you enjoy Psychological Depth' })
    await user.click(screen.getByRole('button', { name: 'See all' }))

    expect(explore).toHaveBeenCalledWith({ concept: 'psychological-depth' })
  })

  it('builds no shelf when there is no established preference', async () => {
    renderPage({ signedIn: true, dashboard: EMPTY_DASHBOARD })

    await screen.findByText('Keep rating works to build your taste profile.')
    expect(screen.queryByText(/Because you enjoy/)).not.toBeInTheDocument()
    expect(requests.some((url) => url.includes('concept='))).toBe(false)
  })

  it('never calls any of this a recommendation', async () => {
    const { container } = renderPage({
      signedIn: true,
      dashboard: withPreference('Psychological Depth', 'psychological-depth'),
    })

    await screen.findByRole('heading', { name: 'Because you enjoy Psychological Depth' })
    const rendered = container.textContent ?? ''
    expect(rendered).not.toMatch(/recommend/i)
    expect(rendered).not.toMatch(/picked for you|chosen for you|top match/i)
  })

  // --- routes out ----------------------------------------------------------

  it('does not repeat the global navigation at the foot of the page', async () => {
    renderPage({ signedIn: true, library: [presentation(ANIME, userState())] })

    await screen.findByRole('heading', { name: 'Recent activity' })

    // TopNav already carries Home / Discover / Library / Your Taste. A second
    // set at the bottom was redundant, and read as a container dropped into
    // the page rather than part of it.
    expect(screen.queryByRole('button', { name: 'All works' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Explore' })).not.toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: 'Preference evidence' }),
    ).not.toBeInTheDocument()
  })

  it('closes with the thesis rather than another navigation surface', async () => {
    renderPage({ signedIn: true, library: [presentation(ANIME, userState())] })

    await screen.findByRole('heading', { name: 'Recent activity' })
    expect(
      screen.getByText(/Noema reads your ratings, not your reasons/),
    ).toBeInTheDocument()
  })

  it('signs the reader out', async () => {
    const user = userEvent.setup()
    renderPage({ signedIn: true, library: [presentation(ANIME, userState())] })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(screen.getByRole('button', { name: 'Log out' }))

    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 1, name: 'Noema' })).toBeInTheDocument(),
    )
  })

  // --- accessibility -------------------------------------------------------

  it('uses one level-1 heading and names every control', async () => {
    renderPage({
      signedIn: true,
      dashboard: withPreference('Psychological Depth', 'psychological-depth'),
      library: [presentation(ANIME, userState())],
    })

    await screen.findByRole('heading', { name: 'Recent activity' })
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(screen.getAllByRole('heading', { level: 2 }).length).toBeGreaterThanOrEqual(3)
    for (const button of screen.getAllByRole('button')) {
      expect(button).toHaveAccessibleName()
    }
  })
})
