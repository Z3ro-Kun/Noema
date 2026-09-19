import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { setSessionToken } from './api/library'
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
      if (url.includes('/summary')) return json(librarySummary())
      if (url.includes('/history')) return json(workHistory())
      if (init?.method === 'POST') return json(presentation(WORK, userState()), 201)
      return json(libraryPage((options.library ?? []) as never[]))
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

/** The main nav, so a nav button is never confused with a body button. */
function nav() {
  return within(screen.getByRole('navigation', { name: 'Main' }))
}

describe('Navigation', () => {
  beforeEach(() => {
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

  it('reaches Your Taste from the nav', async () => {
    const user = userEvent.setup()
    renderApp()

    await screen.findByRole('heading', { level: 1, name: 'Noema' })
    await user.click(nav().getByRole('button', { name: 'Your Taste' }))

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Your Taste' }),
    ).toBeInTheDocument()
    // Signed out, so it asks for an account rather than inventing a profile.
    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('reaches the Library from the nav', async () => {
    const user = userEvent.setup()
    renderApp()

    await screen.findByRole('heading', { level: 1, name: 'Noema' })
    await user.click(nav().getByRole('button', { name: 'Library' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Library' })).toBeInTheDocument()
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
