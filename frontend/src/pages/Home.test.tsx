import { render, screen, waitFor, within } from '@testing-library/react'
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
  recommendationResponse,
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
  /** The recommendation payload, or a failure the page must survive. */
  recommendations?: unknown
  recommendationsFail?: boolean
  /** Marking a recommendation "not interested" fails on the server. */
  dismissFails?: boolean
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
    if (url.includes('/api/v1/recommendations')) {
      if (url.includes('/feedback')) {
        if (options.dismissFails) return fail(503, 'could not save that')
        return Promise.resolve({
          ok: true,
          status: 201,
          json: () =>
            Promise.resolve({
              work_id: url.split('/recommendations/')[1].split('/')[0],
              action: 'not_interested',
              created_at: '2026-09-22T00:00:00Z',
              suppressed_from_recommendations: true,
            }),
        } as Response)
      }
      if (options.recommendationsFail) return fail(503, 'unavailable')
      return json(options.recommendations ?? recommendationResponse())
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

describe('Home is a signed-in surface', () => {
  beforeEach(() => {
    resetSessionForTests()
    setSessionToken(null)
    vi.unstubAllGlobals()
  })

  /**
   * Home used to carry a whole anonymous landing page, and this block used to
   * test it: a proposition, a three-medium directory, a sign-in panel, and
   * the assertion that none of it named a work or called the API.
   *
   * The authentication gate ended that. `App` sends an anonymous reader to
   * `/login` before Home renders, so the branch became unreachable and has
   * been deleted rather than left as a surface nobody can visit. What
   * replaces those tests is the one thing still worth pinning: rendered
   * without a reader, the page shows nobody's shelves and nobody's address.
   */
  it('shows nothing personal when there is no reader', async () => {
    renderPage({ signedIn: false })

    // Not an empty library -- no library at all. The gate means this state is
    // only ever the moment a stored session is being restored, so it says so
    // rather than rendering a reader's sections with nobody's data in them.
    expect(await screen.findByText('Checking your session…')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Recent activity' })).not.toBeInTheDocument()
    expect(screen.queryByText(ACCOUNT.email)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Log out' })).not.toBeInTheDocument()
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
    const activity = (
      await screen.findByRole('heading', { name: 'Recent activity' })
    ).closest('section')
    expect(activity).not.toBeNull()
    // Scoped to the section: the same work may also sit on the recommendation
    // shelf, and "is it in my recent activity" is what this asks.
    expect(within(activity as HTMLElement).getByText('Cowboy Bebop')).toBeInTheDocument()
    // Said as news rather than as a status token.
    expect(within(activity as HTMLElement).getByText('Currently watching')).toBeInTheDocument()
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
    expect(screen.getByText(/From 6 works you have rated/)).toBeInTheDocument()
    // What it rests on, in the reader's own ratings rather than as a grade.
    expect(screen.getByText(/It appears across 5 of your ratings/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'View your full taste profile' }))
    expect(navigate).toHaveBeenCalledWith('taste')
  })

  it('shows no confidence grade anywhere on the taste band', async () => {
    /**
     * The band used to end each row with "moderate confidence". It said
     * almost nothing -- the band is coarse enough that nearly every row read
     * the same -- and it invited an internal number to be read as a
     * percentage. The reader is told what the finding rests on instead.
     */
    renderPage({
      signedIn: true,
      dashboard: withPreference('Psychological Depth', 'psychological-depth'),
    })

    const band = (
      await screen.findByRole('heading', { name: 'What Noema has noticed so far' })
    ).closest('section') as HTMLElement
    const rendered = band.textContent ?? ''

    expect(rendered).not.toMatch(/confidence/i)
    expect(rendered).not.toMatch(/evidence|signal|score|weight/i)
    expect(rendered).not.toMatch(/\d+(\.\d+)?%/)
  })

  it('claims only what it shows', async () => {
    /**
     * "Read from your ratings, nothing else" has to be true of everything in
     * the band. It is: every row comes from a rating-driven group, and
     * reconsumption -- which is behaviour, not a verdict -- is reported on the
     * profile page rather than here.
     */
    renderPage({
      signedIn: true,
      dashboard: withPreference('Psychological Depth', 'psychological-depth'),
    })

    const band = (
      await screen.findByRole('heading', { name: 'What Noema has noticed so far' })
    ).closest('section') as HTMLElement

    expect(band.textContent).toContain('Read from your ratings, nothing else.')
    expect(band.textContent).toContain('Your taste takes shape as you rate more.')
    expect(band.textContent).not.toMatch(/went back to|returned to/i)
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

    const activity = (
      await screen.findByRole('heading', { name: 'Recent activity' })
    ).closest('section')
    expect(within(activity as HTMLElement).getByText('Cowboy Bebop')).toBeInTheDocument()
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
    renderPage({
      signedIn: true,
      dashboard: EMPTY_DASHBOARD,
      // A profile with no established preference cannot produce a
      // recommendation either. Pairing them keeps the fixture a state the
      // API could actually return.
      recommendations: recommendationResponse(
        { state: 'no_activity', established_preferences: 0, candidates_matched: 0 },
        [],
      ),
    })

    await screen.findByText('Keep rating works to build your taste profile.')
    expect(screen.queryByText(/Because you enjoy/)).not.toBeInTheDocument()
    expect(requests.some((url) => url.includes('concept='))).toBe(false)
  })

  it('never calls the theme shelf a recommendation', async () => {
    /**
     * The page now has both: a genuinely ranked shelf, and these theme
     * filters. The distinction is the honest part and it has to survive --
     * a reader can reproduce a theme shelf exactly in Discover, and cannot
     * reproduce a recommendation, so only one of them may claim to be one.
     */
    renderPage({
      signedIn: true,
      dashboard: withPreference('Psychological Depth', 'psychological-depth'),
    })

    const heading = await screen.findByRole('heading', {
      name: 'Because you enjoy Psychological Depth',
    })
    const shelf = heading.closest('section')
    expect(shelf).not.toBeNull()
    const rendered = shelf?.textContent ?? ''
    expect(rendered).not.toMatch(/recommend/i)
    expect(rendered).not.toMatch(/picked for you|chosen for you|top match/i)
    expect(rendered).toMatch(/the same list\s+anyone gets/i)
  })

  // --- recommended for you -------------------------------------------------

  it('shows a recommendation with the preference that produced it', async () => {
    renderPage({ signedIn: true })

    await screen.findByRole('heading', { name: 'Recommended for you' })
    const shelf = screen
      .getByRole('heading', { name: 'Recommended for you' })
      .closest('section')

    expect(shelf?.textContent).toContain('Because you enjoy Psychological Depth')
    // A count the reader can check against their own profile, not a score.
    expect(shelf?.textContent).toContain('From 4 works you rated')
  })

  it('declares a dislike the work also matches', async () => {
    renderPage({ signedIn: true })

    await screen.findByRole('heading', { name: 'Recommended for you' })

    expect(
      screen.getByText(/Although Horror tends not to work for you/),
    ).toBeInTheDocument()
  })

  it('shows no score, percentage or rank on the shelf', async () => {
    renderPage({ signedIn: true })

    await screen.findByRole('heading', { name: 'Recommended for you' })
    const shelf = screen
      .getByRole('heading', { name: 'Recommended for you' })
      .closest('section')
    const rendered = shelf?.textContent ?? ''

    expect(rendered).not.toMatch(/\d+%/)
    expect(rendered).not.toMatch(/match score|confidence: 0|score/i)
  })

  it('asks a cold-start reader to rate rather than inventing a shelf', async () => {
    renderPage({
      signedIn: true,
      recommendations: recommendationResponse(
        { state: 'no_activity', established_preferences: 0, candidates_matched: 0 },
        [],
      ),
    })

    expect(
      await screen.findByText('Rate a few works to start building your recommendations.'),
    ).toBeInTheDocument()
  })

  it('says so when nothing has settled into a pattern yet', async () => {
    renderPage({
      signedIn: true,
      recommendations: recommendationResponse(
        { state: 'building', established_preferences: 0, candidates_matched: 0 },
        [],
      ),
    })

    expect(
      await screen.findByText(/Nothing has settled into a pattern yet/),
    ).toBeInTheDocument()
  })

  it('distinguishes an empty catalogue answer from an empty profile', async () => {
    renderPage({
      signedIn: true,
      recommendations: recommendationResponse(
        { state: 'no_matches', established_preferences: 3, candidates_matched: 0 },
        [],
      ),
    })

    expect(
      await screen.findByText(/Nothing new in the catalogue carries the themes/),
    ).toBeInTheDocument()
  })

  it('survives a recommendation failure without taking the page with it', async () => {
    renderPage({ signedIn: true, recommendationsFail: true })

    await screen.findByRole('heading', { name: 'Recent activity' })
    expect(
      screen.queryByRole('heading', { name: 'Recommended for you' }),
    ).not.toBeInTheDocument()
  })

  it('offers "Not interested" on every recommendation card', async () => {
    renderPage({ signedIn: true })

    await screen.findByRole('heading', { name: 'Recommended for you' })
    const shelf = screen
      .getByRole('heading', { name: 'Recommended for you' })
      .closest('section') as HTMLElement

    expect(
      within(shelf).getAllByRole('button', { name: 'Not interested' }),
    ).toHaveLength(2)
  })

  it('does not offer it on ordinary work cards', async () => {
    /**
     * It belongs to recommendation presentation. A theme shelf is a Discover
     * filter, and "do not recommend this" would mean nothing there.
     */
    renderPage({
      signedIn: true,
      dashboard: withPreference('Psychological Depth', 'psychological-depth'),
      library: [presentation(ANIME, userState())],
    })

    const theme = (
      await screen.findByRole('heading', { name: 'Because you enjoy Psychological Depth' })
    ).closest('section') as HTMLElement
    const activity = (
      screen.getByRole('heading', { name: 'Recent activity' })
    ).closest('section') as HTMLElement

    expect(
      within(theme).queryByRole('button', { name: 'Not interested' }),
    ).not.toBeInTheDocument()
    expect(
      within(activity).queryByRole('button', { name: 'Not interested' }),
    ).not.toBeInTheDocument()
  })

  it('sends the dismissal to the work it was clicked on', async () => {
    const user = userEvent.setup()
    renderPage({ signedIn: true })

    await screen.findByRole('heading', { name: 'Recommended for you' })
    await user.click(screen.getAllByRole('button', { name: 'Not interested' })[0])

    await waitFor(() =>
      expect(
        requests.some((url) => url.includes('/api/v1/recommendations/work-1/feedback')),
      ).toBe(true),
    )
  })

  it('removes the card once the dismissal is saved', async () => {
    const user = userEvent.setup()
    renderPage({ signedIn: true })

    await screen.findByRole('heading', { name: 'Recommended for you' })
    const shelf = screen
      .getByRole('heading', { name: 'Recommended for you' })
      .closest('section') as HTMLElement
    expect(within(shelf).getByText(/Because you enjoy Psychological Depth/)).toBeInTheDocument()

    await user.click(within(shelf).getAllByRole('button', { name: 'Not interested' })[0])

    await waitFor(() =>
      expect(
        within(shelf).queryByText(/Because you enjoy Psychological Depth/),
      ).not.toBeInTheDocument(),
    )
    // The rest of the shelf stays exactly where it was.
    expect(within(shelf).getByText(/Because you enjoy Mystery/)).toBeInTheDocument()
  })

  it('keeps the card and says so when the dismissal fails', async () => {
    /** A card that vanished unsaved would return on the next load. */
    const user = userEvent.setup()
    renderPage({ signedIn: true, dismissFails: true })

    await screen.findByRole('heading', { name: 'Recommended for you' })
    const shelf = screen
      .getByRole('heading', { name: 'Recommended for you' })
      .closest('section') as HTMLElement

    await user.click(within(shelf).getAllByRole('button', { name: 'Not interested' })[0])

    expect(await screen.findByText('That did not save.')).toBeInTheDocument()
    expect(
      within(shelf).getByText(/Because you enjoy Psychological Depth/),
    ).toBeInTheDocument()
  })

  it('never calls a dismissal a dislike', async () => {
    const user = userEvent.setup()
    renderPage({ signedIn: true })

    await screen.findByRole('heading', { name: 'Recommended for you' })
    const shelf = screen
      .getByRole('heading', { name: 'Recommended for you' })
      .closest('section') as HTMLElement
    await user.click(within(shelf).getAllByRole('button', { name: 'Not interested' })[0])

    await waitFor(() => expect(requests.some((u) => u.includes('/feedback'))).toBe(true))
    const rendered = shelf.textContent ?? ''
    expect(rendered).not.toMatch(/dislike|hated|rated it|won.t like/i)
  })

  it('asks for recommendations with no user identifier', async () => {
    renderPage({ signedIn: true })

    await screen.findByRole('heading', { name: 'Recommended for you' })
    const asked = requests.filter((url) => url.includes('/api/v1/recommendations'))

    expect(asked).toHaveLength(1)
    expect(asked[0]).not.toMatch(/user/i)
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
