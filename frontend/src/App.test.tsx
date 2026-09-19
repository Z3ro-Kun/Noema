import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App, { AppRoutes } from './App'
import { setSessionToken } from './api/client'
import { resetSessionForTests, setAuthenticatedForTests } from './auth/session'
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
 * product work page, all under one set of navigation. So this suite is now
 * about *navigation* rather than about any one page's content; the page
 * suites own that.
 *
 * The loop this checks is the product's whole reason to exist:
 *
 *     Home -> Discover -> a work -> add and rate it -> Your Taste
 *
 * There is no router, deliberately (see `App.tsx`), so "navigating" means the
 * view state changed and the right page rendered. That is exactly what a
 * reader experiences, and it is what these assertions look at.
 */

const WORKS = [presentation(WORK), presentation(ANIME)]

interface Options {
  signedIn?: boolean
  library?: unknown[]
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
      return json({
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
      })
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
function renderAt(path: string, options: Options = {}) {
  // Synchronously, not by leaving a token for `restoreSession` to find: a
  // private address is decided on the first render, and a store that is
  // still anonymous at that moment redirects before the restore lands.
  if (options.signedIn) setAuthenticatedForTests(ACCOUNT.email)
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

describe('Navigation', () => {
  beforeEach(() => {
    // The router reads `window.history`, which persists between tests in a
    // file: without this each test starts wherever the last one left off.
    window.history.replaceState({}, '', '/')
    resetSessionForTests()
    setSessionToken(null)
    vi.unstubAllGlobals()
  })

  it('opens on Home with the four destinations always available', async () => {
    renderApp()

    expect(await screen.findByRole('heading', { level: 1, name: 'Noema' })).toBeInTheDocument()
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
    renderApp()

    await screen.findByRole('heading', { level: 1, name: 'Noema' })
    await user.click(nav().getByRole('button', { name: 'Discover' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Discover' })).toBeInTheDocument()
    expect(nav().getByRole('button', { name: 'Discover' })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })

  it('sends an anonymous reader from Your Taste to Login', async () => {
    const user = userEvent.setup()
    renderApp()

    await screen.findByRole('heading', { level: 1, name: 'Noema' })
    await user.click(nav().getByRole('button', { name: 'Your Taste' }))

    // A taste profile is read from a reader's own ratings, so there is
    // nothing to show without an account.
    expect(await screen.findByRole('heading', { level: 1, name: 'Log in' })).toBeInTheDocument()
  })

  it('sends an anonymous reader from Library to Login', async () => {
    const user = userEvent.setup()
    renderApp()

    await screen.findByRole('heading', { level: 1, name: 'Noema' })
    await user.click(nav().getByRole('button', { name: 'Library' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Log in' })).toBeInTheDocument()
  })

  it('returns to the work after signing in from it', async () => {
    // The work page used to ask for authentication by navigating to the
    // Library, so a reader who signed in from a work landed in their library
    // and lost the work they had been reading. The destination is recorded
    // on the way to Login now.
    const user = userEvent.setup()
    renderApp()

    await screen.findByRole('heading', { level: 1, name: 'Noema' })
    await user.click(nav().getByRole('button', { name: 'Discover' }))
    await user.click(
      await screen.findByRole('button', { name: /Alice.*open this work/ }),
    )
    await screen.findByRole('heading', { level: 1, name: /Alice/ })

    await user.click(screen.getByRole('button', { name: 'Log in' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'Log in' })).toBeInTheDocument()

    await user.type(screen.getByLabelText('Email'), 'reader@example.test')
    await user.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    // Back on the same work, not on the Library.
    expect(
      await screen.findByRole('heading', { level: 1, name: /Alice/ }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 1, name: 'Library' })).not.toBeInTheDocument()
  })

  it('reaches the Library from the nav once signed in', async () => {
    const user = userEvent.setup()
    renderApp({ signedIn: true })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(nav().getByRole('button', { name: 'Library' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Library' })).toBeInTheDocument()
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
    const user = userEvent.setup()
    renderApp()

    await screen.findByRole('heading', { level: 1, name: 'Noema' })
    await user.click(nav().getByRole('button', { name: 'Library' }))
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
    renderAt('/works/work-2')

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Cowboy Bebop' }),
    ).toBeInTheDocument()
  })

  it('shows an anonymous visitor the canonical work and nothing personal', async () => {
    renderAt('/works/work-1')

    expect(await screen.findByRole('heading', { level: 1, name: /Alice/ })).toBeInTheDocument()
    expect(screen.getByText(/Sign in to track this/)).toBeInTheDocument()
    expect(screen.queryByLabelText(/Status for/)).not.toBeInTheDocument()
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
    renderAt('/nothing-here')

    expect(await screen.findByRole('heading', { level: 1, name: 'Noema' })).toBeInTheDocument()
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

  it('does not loop between a private address and Login', async () => {
    renderAt('/library')

    expect(await screen.findByRole('heading', { level: 1, name: 'Log in' })).toBeInTheDocument()
    // One Login, settled: a loop would re-render it endlessly or land back
    // on the private page.
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(screen.getAllByRole('heading', { level: 1, name: 'Log in' })).toHaveLength(1)
  })

  // --- signing out ---------------------------------------------------------

  it('lands on Home after signing out of the Library', async () => {
    // The guard has to tell two situations apart: arriving without an
    // account (-> Login, come back afterwards) and giving one up while
    // already here (-> Home). It used to do neither, so logging out of a
    // private page immediately asked for a login.
    const user = userEvent.setup()
    renderApp({ signedIn: true })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(nav().getByRole('button', { name: 'Library' }))
    await screen.findByRole('heading', { level: 1, name: 'Library' })

    await user.click(screen.getByRole('button', { name: 'Log out' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Noema' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 1, name: 'Log in' })).not.toBeInTheDocument()
  })

  it('lands on Home after signing out of Your Taste', async () => {
    const user = userEvent.setup()
    renderApp({ signedIn: true })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(nav().getByRole('button', { name: 'Your Taste' }))
    await screen.findByRole('heading', { level: 1, name: 'Your Taste' })

    await user.click(screen.getByRole('button', { name: 'Log out' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Noema' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 1, name: 'Log in' })).not.toBeInTheDocument()
  })

  it('still sends an anonymous arrival to Login rather than Home', async () => {
    // The guard is not weakened: only the sign-out case changed.
    const user = userEvent.setup()
    renderApp()

    await screen.findByRole('heading', { level: 1, name: 'Noema' })
    await user.click(nav().getByRole('button', { name: 'Library' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Log in' })).toBeInTheDocument()
  })

  it('shows no personal data after signing out', async () => {
    const user = userEvent.setup()
    renderApp({ signedIn: true, library: [presentation(WORK, userState())] })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(screen.getByRole('button', { name: 'Log out' }))
    await screen.findByRole('heading', { level: 1, name: 'Noema' })

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

  it('carries a domain chosen on Home into Discover as a filter', async () => {
    const user = userEvent.setup()
    renderApp()

    await screen.findByRole('heading', { level: 1, name: 'Noema' })
    await user.click(screen.getByRole('button', { name: 'Anime' }))

    await screen.findByRole('heading', { level: 1, name: 'Discover' })
    await waitFor(() =>
      expect((screen.getByLabelText('Medium') as HTMLSelectElement).value).toBe('anime'),
    )
    expect(await screen.findByText('Cowboy Bebop')).toBeInTheDocument()
  })

  it('opens the corpus viewer from the work page, and keeps it separate', async () => {
    const user = userEvent.setup()
    renderApp({ signedIn: true })

    await screen.findByRole('heading', { level: 1, name: 'Welcome back' })
    await user.click(nav().getByRole('button', { name: 'Discover' }))
    await user.click(await screen.findByText("Alice's Adventures in Wonderland"))

    await screen.findByRole('heading', { name: 'Synopsis' })
    // The product page says nothing about adapters; the viewer is where that
    // lives, and it is labelled as a development surface.
    expect(screen.getByText(/the development surface/)).toBeInTheDocument()
  })

  it('reaches retrieval detail from Discover’s meaning search', async () => {
    const user = userEvent.setup()
    renderApp()

    await screen.findByRole('heading', { level: 1, name: 'Noema' })
    await user.click(nav().getByRole('button', { name: 'Discover' }))
    await user.click(await screen.findByRole('button', { name: 'By meaning' }))

    expect(await screen.findByText(/Describe what you are in the mood for/)).toBeInTheDocument()
  })
})
