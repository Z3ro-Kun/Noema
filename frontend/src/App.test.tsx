import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App, { AppRoutes } from './App'
import { setSessionToken } from './api/client'
import {
  resetSessionForTests,
  setAuthenticatedForTests,
  setRestoringForTests,
} from './auth/session'
import {
  ACCOUNT,
  ANIME,
  FACETS,
  WORK,
  libraryPage,
  librarySummary,
  listPage,
  presentation,
  userState,
  workHistory,
} from './test/fixtures'

/**
 * Moving around the product.
 *
 * Phase 1Y replaced the development landing page -- a corpus list, a health
 * panel and links to every debugging surface -- with Home, Discover and a
 * product work page, all under one set of navigation. So this suite is about
 * *navigation* rather than about any one page's content; the page suites own
 * that.
 *
 * The loop it checks is the product's whole reason to exist:
 *
 *     Home -> Discover -> a work -> add and rate it -> Your Taste
 *
 * ---
 *
 * The gate
 *
 * Noema is not a public catalogue. Every one of those addresses is behind a
 * session, decided in one place in `App.tsx` -- a layout route, not a check
 * repeated per page -- so what this suite asserts about the gate is asserted
 * about the whole application at once:
 *
 *     without a session      any application address -> Login, and the
 *                            address is kept so signing in returns to it
 *     with a session         Login and Register step aside
 *     while restoring        neither: nothing protected is rendered and no
 *                            redirect is issued until the session is known
 *
 * That third line is the one worth stating out loud. A reload starts with a
 * stored token and no answer yet, and a gate that treats "not yet known" as
 * "anonymous" throws the reader out of their own session on every refresh.
 */

const WORKS = [presentation(WORK), presentation(ANIME)]

const EMPTY_DASHBOARD = {
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

/** A profile with one established preference, which is what raises a shelf. */
const WITH_PREFERENCE = {
  ...EMPTY_DASHBOARD,
  summary: { ...EMPTY_DASHBOARD.summary, profile_state: 'established', rated_works: 6 },
  strongly_likes: [
    {
      key: 'psychological-depth',
      display_name: 'Psychological Depth',
      features: [{ key: 'psychological-depth', name: 'Psychological Depth' }],
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

interface Options {
  signedIn?: boolean
  library?: unknown[]
  dashboard?: unknown
  /** Start as a page reload does: a stored token, not yet answered for. */
  restoring?: boolean
}

function mockApi(options: Options = {}) {
  return vi.fn((input: string | URL, init?: RequestInit) => {
    const url = String(input)
    const json = (body: unknown, status = 200) =>
      Promise.resolve({ ok: true, status, json: () => Promise.resolve(body) } as Response)

    if (url.includes('/auth/me')) {
      return options.signedIn
        ? json(ACCOUNT)
        : Promise.resolve({ ok: false, status: 401, json: () => Promise.resolve({}) } as Response)
    }
    if (url.includes('/auth/logout')) {
      return Promise.resolve({ ok: true, status: 204 } as Response)
    }
    if (url.includes('/auth/login') || url.includes('/auth/register')) {
      return json(
        {
          access_token: 'test-token-abc',
          token_type: 'bearer',
          expires_at: '2026-10-02T00:00:00Z',
          user: ACCOUNT,
        },
        url.includes('/auth/register') ? 201 : 200,
      )
    }
    if (url.includes('/preferences/dashboard')) {
      return json(options.dashboard ?? EMPTY_DASHBOARD)
    }
    if (url.includes('/preferences/feedback')) return json({ items: [] })
    if (url.includes('/api/v1/library')) {
      const held = (options.library ?? []) as { user_state?: { status: string } }[]
      if (url.includes('/summary')) {
        // Derived from the same list the listing serves, so the page's tab
        // counts and its rows cannot disagree inside a test.
        const counts: Record<string, number> = {}
        for (const entry of held) {
          const status = entry.user_state?.status
          if (status) counts[status] = (counts[status] ?? 0) + 1
        }
        return json(librarySummary(counts))
      }
      if (url.includes('/history')) return json(workHistory())
      if (init?.method === 'POST') return json(presentation(WORK, userState()), 201)
      // Filtered the way the server filters: the Library asks one status at
      // a time, and handing it everything would put one work in five groups.
      const status = new URL(url, 'http://localhost').searchParams.get('status')
      const items = status
        ? held.filter((entry) => entry.user_state?.status === status)
        : held
      return json(libraryPage(items as never[]))
    }
    if (url.includes('/works/facets')) return json(FACETS)
    if (url.match(/\/works\/work-\d+$/)) {
      return json(url.includes('work-2') ? presentation(ANIME) : presentation(WORK))
    }
    if (url.includes('/api/v1/works')) {
      if (url.includes('domain=anime')) return json(listPage([presentation(ANIME)]))
      if (url.includes('q=')) return json(listPage([presentation(WORK)]))
      return json(listPage(WORKS))
    }
    return Promise.reject(new Error(`unexpected request: ${url}`))
  })
}

function renderApp(options: Options = {}) {
  if (options.signedIn) setSessionToken('test-token-abc')
  vi.stubGlobal('fetch', mockApi(options))
  return render(<App />)
}

/**
 * Mount the route table at a chosen address.
 *
 * A direct link is the thing the router exists for, and it cannot be tested
 * by clicking: the reader arrives with the URL already set.
 */
function renderAt(path: string | { pathname: string; state?: unknown }, options: Options = {}) {
  // Synchronously, not by leaving a token for `restoreSession` to find: a
  // private address is decided on the first render, and a store that is
  // still anonymous at that moment redirects before the restore lands.
  if (options.restoring) setRestoringForTests()
  else if (options.signedIn) setAuthenticatedForTests(ACCOUNT.email)
  vi.stubGlobal('fetch', mockApi(options))
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  )
}

/** The main nav, so a nav button is never confused with a body button. */
function nav() {
  return within(screen.getByRole('navigation', { name: 'Main' }))
}

/** The application shell itself, which only a signed-in reader ever sees. */
function expectOutsideTheApplication() {
  expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument()
}

describe('Navigation', () => {
  beforeEach(() => {
    // The router reads `window.history`, which persists between tests in a
    // file: without this each test starts wherever the last one left off.
    window.history.replaceState({}, '', '/')
    resetSessionForTests()
    setSessionToken(null)
    vi.unstubAllGlobals()
  })

  it('opens on Login for a reader without an account', async () => {
    renderApp()

    expect(await screen.findByRole('heading', { level: 1, name: 'Log in' })).toBeInTheDocument()
    expectOutsideTheApplication()
  })

  it('opens on Home with the four destinations once signed in', async () => {
    renderApp({ signedIn: true })

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Welcome back' }),
    ).toBeInTheDocument()
    for (const label of ['Home', 'Discover', 'Library', 'Your Taste']) {
      expect(nav().getByRole('button', { name: label })).toBeInTheDocument()
    }
    expect(nav().getByRole('button', { name: 'Home' })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })

  it('reaches Discover from the nav', async () => {
    const user = userEvent.setup()
    renderApp({ signedIn: true })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(nav().getByRole('button', { name: 'Discover' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Discover' })).toBeInTheDocument()
    expect(nav().getByRole('button', { name: 'Discover' })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })

  it('reaches the Library from the nav once signed in', async () => {
    const user = userEvent.setup()
    renderApp({ signedIn: true })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(nav().getByRole('button', { name: 'Library' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Library' })).toBeInTheDocument()
  })

  // --- the gate ------------------------------------------------------------

  it.each(['/', '/discover', '/library', '/taste', '/works/work-1', '/nothing-here'])(
    'sends a reader without a session from %s to Login',
    async (path) => {
      renderAt(path)

      expect(
        await screen.findByRole('heading', { level: 1, name: 'Log in' }),
      ).toBeInTheDocument()
      expectOutsideTheApplication()
    },
  )

  it('keeps Login reachable without a session', async () => {
    renderAt('/login')

    expect(await screen.findByRole('heading', { level: 1, name: 'Log in' })).toBeInTheDocument()
  })

  it('keeps Register reachable without a session', async () => {
    renderAt('/register')

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Create an account' }),
    ).toBeInTheDocument()
  })

  it('sends a signed-in reader away from Login and into the product', async () => {
    renderAt('/login', { signedIn: true })

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Welcome back' }),
    ).toBeInTheDocument()
  })

  it('refuses to follow a remembered destination that is not ours', async () => {
    // `from` only ever comes from this application's own navigations, so an
    // external address cannot get into it through a query string. The check
    // exists anyway: `//example.com` reads as a path to the router and as
    // another origin to the browser, and Home is the right answer for it.
    renderAt(
      { pathname: '/login', state: { from: '//example.invalid/phish' } },
      { signedIn: true },
    )

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Welcome back' }),
    ).toBeInTheDocument()
  })

  it('sends a signed-in reader away from Register too', async () => {
    renderAt('/register', { signedIn: true })

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Welcome back' }),
    ).toBeInTheDocument()
  })

  it('exposes nothing while a stored session is still being checked', async () => {
    // The refresh case. Until `/auth/me` answers there is no honest verdict,
    // so the gate renders neither the protected page nor a redirect to Login
    // -- a reader reloading their own library must not be bounced out of it
    // and must not see their library before the session is confirmed either.
    renderAt('/library', { signedIn: true, restoring: true })

    expect(screen.queryByRole('heading', { level: 1, name: 'Library' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 1, name: 'Log in' })).not.toBeInTheDocument()

    expect(await screen.findByRole('heading', { level: 1, name: 'Library' })).toBeInTheDocument()
  })

  it('sends a reader whose stored session is no longer valid to Login', async () => {
    // Same starting point, opposite answer: the token is there but the server
    // rejects it, and an expired session buys no access to anything.
    renderAt('/taste', { restoring: true })

    expect(await screen.findByRole('heading', { level: 1, name: 'Log in' })).toBeInTheDocument()
    expect(
      screen.queryByRole('heading', { level: 1, name: 'Your Taste' }),
    ).not.toBeInTheDocument()
  })

  // --- the browser's own history -------------------------------------------

  it('puts every move in the browser history', async () => {
    const user = userEvent.setup()
    renderApp({ signedIn: true })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(nav().getByRole('button', { name: 'Discover' }))
    await screen.findByRole('heading', { level: 1, name: 'Discover' })
    await user.click(await screen.findByRole('button', { name: /Alice.*open this work/ }))
    await screen.findByRole('heading', { level: 1, name: /Alice/ })
    expect(window.location.pathname).toBe('/works/work-1')

    // Back to Discover, then back to Home: the browser's own entries.
    window.history.back()
    expect(await screen.findByRole('heading', { level: 1, name: 'Discover' })).toBeInTheDocument()

    window.history.back()
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Welcome back' }),
    ).toBeInTheDocument()

    // And forward again.
    window.history.forward()
    expect(await screen.findByRole('heading', { level: 1, name: 'Discover' })).toBeInTheDocument()
  })

  it('steps back from one work to the work before it', async () => {
    const user = userEvent.setup()
    renderApp({ signedIn: true })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(nav().getByRole('button', { name: 'Discover' }))
    await user.click(await screen.findByRole('button', { name: /Alice.*open this work/ }))
    await screen.findByRole('heading', { level: 1, name: /Alice/ })

    await user.click(screen.getByRole('button', { name: 'Back' }))
    await user.click(await screen.findByRole('button', { name: /Cowboy Bebop.*open this work/ }))
    await screen.findByRole('heading', { level: 1, name: 'Cowboy Bebop' })

    window.history.back()
    // Discover sits between them, because that is where the reader went.
    expect(await screen.findByRole('heading', { level: 1, name: 'Discover' })).toBeInTheDocument()
    window.history.back()
    expect(await screen.findByRole('heading', { level: 1, name: /Alice/ })).toBeInTheDocument()
  })

  it('steps back from Register to Login', async () => {
    // `renderApp`, not `renderAt`: this is about the browser's own history,
    // which a MemoryRouter does not have.
    const user = userEvent.setup()
    renderApp()

    await screen.findByRole('heading', { level: 1, name: 'Log in' })

    await user.click(screen.getByRole('button', { name: 'Create an account' }))
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Create an account' }),
    ).toBeInTheDocument()

    window.history.back()
    expect(await screen.findByRole('heading', { level: 1, name: 'Log in' })).toBeInTheDocument()
  })

  // --- addresses -----------------------------------------------------------

  it('opens a work straight from its own address', async () => {
    renderAt('/works/work-2', { signedIn: true })

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Cowboy Bebop' }),
    ).toBeInTheDocument()
  })

  it('shows a signed-in reader their own state at the same address', async () => {
    renderAt('/works/work-1', { signedIn: true })

    await screen.findByRole('heading', { level: 1, name: /Alice/ })
    // The same address, but the reader's own half is live rather than a
    // prompt: this work is not in their library yet, so adding it is offered.
    expect(await screen.findByRole('button', { name: 'Add to library' })).toBeInTheDocument()
    expect(screen.queryByText(/Sign in to track this/)).not.toBeInTheDocument()
  })

  it('sends an address Noema does not have back to Home', async () => {
    renderAt('/nothing-here', { signedIn: true })

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Welcome back' }),
    ).toBeInTheDocument()
  })

  it('opens a private address directly, once there is an account', async () => {
    renderAt('/library', { signedIn: true })

    expect(await screen.findByRole('heading', { level: 1, name: 'Library' })).toBeInTheDocument()
  })

  it('sends an anonymous visitor from a private address to Login, and back after', async () => {
    const user = userEvent.setup()
    renderAt('/taste')

    expect(await screen.findByRole('heading', { level: 1, name: 'Log in' })).toBeInTheDocument()

    await user.type(screen.getByLabelText('Email'), 'reader@example.test')
    await user.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    // The destination they asked for, not Home.
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Your Taste' }),
    ).toBeInTheDocument()
  })

  it('returns to the work after signing in from its address', async () => {
    // A shared link is the case that makes deep-link preservation worth
    // having: the reader arrives at a work, has to sign in on the way, and
    // must land on the work rather than on Home or on their Library.
    const user = userEvent.setup()
    renderAt('/works/work-1')

    await screen.findByRole('heading', { level: 1, name: 'Log in' })
    expect(screen.queryByRole('heading', { level: 1, name: /Alice/ })).not.toBeInTheDocument()

    await user.type(screen.getByLabelText('Email'), 'reader@example.test')
    await user.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    expect(await screen.findByRole('heading', { level: 1, name: /Alice/ })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 1, name: 'Library' })).not.toBeInTheDocument()
  })

  it('does not loop between a private address and Login', async () => {
    renderAt('/library')

    expect(await screen.findByRole('heading', { level: 1, name: 'Log in' })).toBeInTheDocument()
    // One Login, settled: a loop would re-render it endlessly or land back
    // on the private page.
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(screen.getAllByRole('heading', { level: 1, name: 'Log in' })).toHaveLength(1)
  })

  // --- signing out ---------------------------------------------------------

  it('lands on Login after signing out of the Library', async () => {
    // Signing out leaves the application entirely. There is no signed-out
    // version of Noema to be dropped on, so the only honest destination is
    // the login page -- and the reader must not be left looking at a private
    // page for a frame on the way there.
    const user = userEvent.setup()
    renderApp({ signedIn: true })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(nav().getByRole('button', { name: 'Library' }))
    await screen.findByRole('heading', { level: 1, name: 'Library' })

    await user.click(screen.getByRole('button', { name: 'Log out' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Log in' })).toBeInTheDocument()
    expectOutsideTheApplication()
  })

  it('lands on Login after signing out of Your Taste', async () => {
    const user = userEvent.setup()
    renderApp({ signedIn: true })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(nav().getByRole('button', { name: 'Your Taste' }))
    await screen.findByRole('heading', { level: 1, name: 'Your Taste' })

    await user.click(screen.getByRole('button', { name: 'Log out' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Log in' })).toBeInTheDocument()
    expectOutsideTheApplication()
  })

  it('forgets the page signed out of rather than returning to it', async () => {
    // Deep-link preservation and signing out pull in opposite directions,
    // and the difference is intent: an anonymous arrival was *trying* to
    // reach that page, while someone who pressed Log out was finished with
    // it. Signing back in therefore opens Home, not the Library.
    const user = userEvent.setup()
    renderApp({ signedIn: true })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(nav().getByRole('button', { name: 'Library' }))
    await screen.findByRole('heading', { level: 1, name: 'Library' })
    await user.click(screen.getByRole('button', { name: 'Log out' }))
    await screen.findByRole('heading', { level: 1, name: 'Log in' })

    await user.type(screen.getByLabelText('Email'), ACCOUNT.email)
    await user.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Welcome back' }),
    ).toBeInTheDocument()
  })

  it('shows no personal data after signing out', async () => {
    const user = userEvent.setup()
    renderApp({ signedIn: true, library: [presentation(WORK, userState())] })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(screen.getByRole('button', { name: 'Log out' }))
    await screen.findByRole('heading', { level: 1, name: 'Log in' })

    expect(screen.queryByText(ACCOUNT.email)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Log out' })).not.toBeInTheDocument()
    expect(screen.queryByText("Alice's Adventures in Wonderland")).not.toBeInTheDocument()
  })

  // --- where Back goes -----------------------------------------------------

  it('returns to the Library from a work opened in the Library', async () => {
    const user = userEvent.setup()
    renderApp({ signedIn: true, library: [presentation(WORK, userState())] })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(nav().getByRole('button', { name: 'Library' }))
    await user.click(
      await screen.findByRole('button', { name: /Alice.*open this work/ }),
    )
    await screen.findByRole('heading', { level: 1, name: /Alice/ })

    await user.click(screen.getByRole('button', { name: 'Back' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Library' })).toBeInTheDocument()
  })

  it('returns to Discover from a work opened in Discover', async () => {
    const user = userEvent.setup()
    renderApp({ signedIn: true })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(nav().getByRole('button', { name: 'Discover' }))
    await user.click(
      await screen.findByRole('button', { name: /Alice.*open this work/ }),
    )
    await screen.findByRole('heading', { level: 1, name: /Alice/ })

    await user.click(screen.getByRole('button', { name: 'Back' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Discover' })).toBeInTheDocument()
  })

  it('returns Home from a work opened on Home', async () => {
    const user = userEvent.setup()
    renderApp({ signedIn: true, library: [presentation(WORK, userState())] })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(
      await screen.findByRole('button', { name: /Alice.*open this work/ }),
    )
    await screen.findByRole('heading', { level: 1, name: /Alice/ })

    await user.click(screen.getByRole('button', { name: 'Back' }))

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Welcome back' }),
    ).toBeInTheDocument()
  })

  // --- the account affordance ----------------------------------------------

  it('offers Log out from every product surface once signed in', async () => {
    const user = userEvent.setup()
    renderApp({ signedIn: true, library: [presentation(WORK, userState())] })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    expect(screen.getByRole('button', { name: 'Log out' })).toBeInTheDocument()

    for (const destination of ['Discover', 'Library', 'Your Taste'] as const) {
      await user.click(nav().getByRole('button', { name: destination }))
      await screen.findByRole('heading', { level: 1, name: destination === 'Your Taste' ? 'Your Taste' : destination })
      expect(screen.getByRole('button', { name: 'Log out' })).toBeInTheDocument()
    }

    // And on a work page, where it used to be absent entirely.
    await user.click(nav().getByRole('button', { name: 'Discover' }))
    await user.click(await screen.findByRole('button', { name: /Alice.*open this work/ }))
    await screen.findByRole('heading', { level: 1, name: /Alice/ })
    expect(screen.getByRole('button', { name: 'Log out' })).toBeInTheDocument()
  })

  it('walks the product loop: discover a work, open it, reach your taste', async () => {
    const user = userEvent.setup()
    renderApp({ signedIn: true })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(nav().getByRole('button', { name: 'Discover' }))

    await user.click(await screen.findByText("Alice's Adventures in Wonderland"))

    // The product work page, not the corpus viewer.
    expect(
      await screen.findByRole('heading', {
        level: 1,
        name: "Alice's Adventures in Wonderland",
      }),
    ).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Synopsis' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'You and this work' })).toBeInTheDocument()

    // Your Taste is one nav click from anywhere, including a work page. The
    // in-panel shortcut appears once a work is actually held and rated, which
    // the WorkPage suite covers directly.
    await user.click(nav().getByRole('button', { name: 'Your Taste' }))
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Your Taste' }),
    ).toBeInTheDocument()
  })

  it('carries a theme chosen on Home into Discover as a filter', async () => {
    // Home offers Discover a filter, and Discover has to arrive already
    // filtered rather than showing everything and being corrected. The
    // anonymous landing page used to be where this was exercised, from its
    // medium chips; the same wiring now runs from a signed-in theme shelf.
    const user = userEvent.setup()
    renderApp({ signedIn: true, dashboard: WITH_PREFERENCE })

    await screen.findByRole('heading', { name: 'Because you enjoy Psychological Depth' })
    await user.click(screen.getByRole('button', { name: 'See all' }))

    await screen.findByRole('heading', { level: 1, name: 'Discover' })
    await waitFor(() =>
      expect((screen.getByLabelText('Theme') as HTMLSelectElement).value).toBe(
        'psychological-depth',
      ),
    )
  })

  it('links the record viewer from the work page, and keeps it separate', async () => {
    const user = userEvent.setup()
    renderApp({ signedIn: true })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(nav().getByRole('button', { name: 'Discover' }))
    await user.click(await screen.findByText("Alice's Adventures in Wonderland"))

    await screen.findByRole('heading', { name: 'Synopsis' })
    // The product page says nothing about adapters; the viewer is where that
    // lives, and the link says plainly that it is a development surface.
    expect(
      screen.getByRole('button', { name: 'See where this record came from' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/a development surface/)).toBeInTheDocument()
  })

  it('reaches the theme search from Discover', async () => {
    const user = userEvent.setup()
    renderApp({ signedIn: true })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(nav().getByRole('button', { name: 'Discover' }))
    await user.click(await screen.findByRole('button', { name: 'By theme' }))

    expect(await screen.findByText(/Describe what you are in the mood for/)).toBeInTheDocument()
  })
})
