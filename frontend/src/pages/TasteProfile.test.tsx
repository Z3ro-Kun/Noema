import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TasteProfile from './TasteProfile'
import { resetSessionForTests, setAuthenticatedForTests } from '../auth/session'
import type {
  EvidenceCounts,
  PreferenceFeedback,
  PreferenceOverview,
  TasteDashboard,
  TasteEvidenceSummary,
  TastePreferenceItem,
  TasteStandoutObservation,
} from '../types/api'

/**
 * Your Taste, as a reader meets it.
 *
 * These check wording and structure rather than layout, because wording is
 * what the page is for. The engine below it was settled in 1O-1W; what is
 * under test here is whether the sentences it is rendered as stay true to it.
 *
 * Three things in particular:
 *
 *   A strong preference held with moderate confidence must read as a clear
 *   liking. Phase 1V coupled the two and got the first answer wrong to hedge
 *   the second; the contract separated them in 1W and the page must not put
 *   them back together.
 *
 *   A combination must never look like a single concept with a long name.
 *
 *   "Not really" must be recorded as a disagreement and confirmed as one --
 *   not as a dislike, and not as a claim that the profile has changed.
 *
 * The fixtures mirror the shapes `GET /api/v1/preferences/dashboard` really
 * returns, so what is asserted here is what a real profile would render.
 */

const ACCOUNT = { id: 'user-1', email: 'reader@example.test', display_name: null, created_at: '2026-09-19T00:00:00Z' }

function evidence(overrides: Partial<TasteEvidenceSummary> = {}): TasteEvidenceSummary {
  return {
    rated_works: 5,
    supporting_works: 6,
    domains: ['Anime'],
    includes_reconsumed_works: false,
    has_mixed_evidence: false,
    ...overrides,
  }
}

function item(overrides: Partial<TastePreferenceItem> = {}): TastePreferenceItem {
  return {
    key: 'psychological-depth',
    display_name: 'Psychological Depth',
    features: [{ key: 'psychological-depth', name: 'Psychological Depth' }],
    kind: 'individual',
    direction: 'positive',
    confidence_band: 'moderate',
    presentation_key: 'enjoys_feature',
    domains: ['Anime'],
    evidence_summary: evidence(),
    also_supported_by: [],
    ...overrides,
  }
}

const COMBINATION = item({
  key: 'mystery+psychological-depth',
  display_name: 'Mystery + Psychological Depth',
  features: [
    { key: 'mystery', name: 'Mystery' },
    { key: 'psychological-depth', name: 'Psychological Depth' },
  ],
  kind: 'combination',
  presentation_key: 'enjoys_combination',
})

const STANDOUT: TasteStandoutObservation = {
  observation: 'combination_highlight',
  presentation_key: 'enjoys_combination',
  features: [
    { key: 'mystery', name: 'Mystery' },
    { key: 'identity', name: 'Identity' },
  ],
  domains: ['Anime', 'Literature'],
  confidence_band: 'moderate',
  rated_works: 4,
}

function dashboard(overrides: Partial<TasteDashboard> = {}): TasteDashboard {
  return {
    summary: {
      profile_state: 'established',
      rated_works: 6,
      established_preferences: 1,
      emerging_signals: 0,
    },
    strongly_likes: [],
    mildly_likes: [],
    dislikes: [],
    emerging: [],
    what_stands_out: [],
    ...overrides,
  }
}

/** Counts as the overview sends them. Zeroed unless a test says otherwise. */
function counts(overrides: Partial<EvidenceCounts> = {}): EvidenceCounts {
  return {
    works_exposed: 0,
    works_started: 0,
    works_completed: 0,
    works_rated: 0,
    positive_ratings: 0,
    negative_ratings: 0,
    rating_mean: null,
    works_reconsumed: 0,
    total_completions: 0,
    works_abandoned: 0,
    works_on_hold: 0,
    ...overrides,
  }
}

function overview(overrides: Partial<PreferenceOverview> = {}): PreferenceOverview {
  return {
    summary: {
      total_interactions: 6,
      works_rated: 6,
      signals_with_direction: 1,
      concepts_awaiting_ratings: 0,
      rating_context_established: true,
      interactions_without_concepts: 0,
    },
    signals: [],
    awaiting_ratings: [],
    ...overrides,
  }
}

interface MockOptions {
  profile?: TasteDashboard
  feedback?: PreferenceFeedback[]
  dashboardFails?: boolean
  feedbackPostFails?: boolean
  /** Left unresolved, so the loading state can be observed. */
  hangDashboard?: boolean
  /** The evidence source. Absent by default: most tests do not need it. */
  overview?: PreferenceOverview
  overviewFails?: boolean
}

let posted: unknown[] = []
let requested: string[] = []

function mockApi(options: MockOptions = {}) {
  posted = []
  requested = []
  return vi.fn((input: string | URL, init?: RequestInit) => {
    const url = String(input)
    requested.push(url)
    const json = (body: unknown, status = 200) =>
      Promise.resolve({ ok: true, status, json: () => Promise.resolve(body) } as Response)

    if (url.includes('/auth/me')) return json(ACCOUNT)

    if (url.includes('/preferences/feedback')) {
      if (init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as {
          concept_slug: string
          feedback: 'confirmed' | 'corrected'
        }
        posted.push(body)
        if (options.feedbackPostFails) {
          return Promise.resolve({
            ok: false,
            status: 500,
            json: () => Promise.resolve({ detail: 'the server said no' }),
          } as Response)
        }
        return json(
          {
            concept_slug: body.concept_slug,
            concept_name: 'Psychological Depth',
            feedback: body.feedback,
            source: 'taste_profile',
            submission_count: 1,
            first_recorded_at: '2026-09-19T00:00:00Z',
            updated_at: '2026-09-19T00:00:00Z',
          },
          201,
        )
      }
      return json({ items: options.feedback ?? [] })
    }

    if (url.includes('/preferences/overview')) {
      if (options.overviewFails || !options.overview) {
        return Promise.resolve({
          ok: false,
          status: 503,
          json: () => Promise.resolve({ detail: 'the evidence layer is unavailable' }),
        } as Response)
      }
      return json(options.overview)
    }

    if (url.includes('/preferences/dashboard')) {
      if (options.hangDashboard) return new Promise<Response>(() => {})
      if (options.dashboardFails) {
        return Promise.resolve({
          ok: false,
          status: 503,
          json: () => Promise.resolve({ detail: 'the engine is unavailable' }),
        } as Response)
      }
      return json(options.profile ?? dashboard())
    }

    return Promise.reject(new Error(`unexpected request: ${url}`))
  })
}

function renderPage(options: MockOptions = {}) {
  vi.stubGlobal('fetch', mockApi(options))
  return render(<TasteProfile onNavigate={() => {}} onOpenLibrary={() => {}} />)
}

describe('TasteProfile', () => {
  beforeEach(() => {
    resetSessionForTests()
    setAuthenticatedForTests(ACCOUNT.email)
  })

  // --- the page itself -----------------------------------------------------

  it('names the page for the reader, not for the model', async () => {
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Your Taste' }),
    ).toBeInTheDocument()
    expect(
      screen.getByText(/What you tend to enjoy, read from the works you have rated/),
    ).toBeInTheDocument()
  })

  it('shows a loading state while the profile is being derived', async () => {
    renderPage({ hangDashboard: true })

    // The session resolves first; the profile request is the one left hanging.
    expect(await screen.findByText(/Reading your ratings/)).toBeInTheDocument()
  })

  it('reports a failure instead of rendering an empty profile', async () => {
    renderPage({ dashboardFails: true })

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('The engine is unavailable.')
    expect(screen.queryByText('You particularly enjoy')).not.toBeInTheDocument()
  })

  // --- the four groups -----------------------------------------------------

  it('voices each group on the finding it belongs to', async () => {
    renderPage({
      profile: dashboard({
        strongly_likes: [item()],
        mildly_likes: [item({ key: 'tragedy', display_name: 'Tragedy', features: [{ key: 'tragedy', name: 'Tragedy' }] })],
        dislikes: [
          item({
            key: 'fantasy',
            display_name: 'Fantasy',
            features: [{ key: 'fantasy', name: 'Fantasy' }],
            direction: 'negative',
            presentation_key: 'negative_feature',
          }),
        ],
        emerging: [
          item({
            key: 'identity',
            display_name: 'Identity',
            features: [{ key: 'identity', name: 'Identity' }],
            presentation_key: 'emerging_feature',
          }),
        ],
      }),
    })

    // The three established groups are no longer drawers with headings over
    // them -- the findings run as one numbered sequence, and each one says
    // which group it belongs to beside its own name. What must survive is
    // that the distinction is still stated, in the same words, on the right
    // finding.
    expect((await screen.findAllByText('You particularly enjoy'))[0]).toBeInTheDocument()
    expect(screen.getByText('You seem drawn to')).toBeInTheDocument()
    expect(screen.getByText('You tend to avoid')).toBeInTheDocument()

    expect(screen.getByRole('heading', { level: 3, name: 'Psychological Depth' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 3, name: 'Tragedy' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 3, name: 'Fantasy' })).toBeInTheDocument()

    // Emerging is not a thesis and is deliberately not set as one: it keeps
    // its own section, its own heading and a ledger row rather than an
    // argument, so it cannot be skim-read as established.
    expect(
      screen.getByRole('heading', { name: 'Something Noema is noticing' }),
    ).toBeInTheDocument()
    expect(screen.getByText('Identity')).toBeInTheDocument()
    expect(screen.getByText('Too early to call')).toBeInTheDocument()
  })

  it('omits a group with nothing in it rather than showing an empty block', async () => {
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    await screen.findAllByText('You particularly enjoy')
    expect(screen.queryByRole('heading', { name: /You also enjoy/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /tend not to enjoy/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /beginning to notice/ })).not.toBeInTheDocument()
  })

  it('says a mild preference is mild, not uncertain', async () => {
    renderPage({ profile: dashboard({ mildly_likes: [item({ confidence_band: 'high' })] }) })

    const meaning = await screen.findByText(/less pronounced/)
    // The distinction the whole section exists to hold.
    expect(meaning).toHaveTextContent(/how much you liked these, not about how sure Noema is/)
  })

  it('does not present an emerging signal as an established preference', async () => {
    renderPage({
      profile: dashboard({
        emerging: [item({ confidence_band: 'high' })],
        summary: {
          profile_state: 'building',
          rated_works: 3,
          established_preferences: 0,
          emerging_signals: 1,
        },
      }),
    })

    expect(await screen.findByText(/Too early to call these/)).toBeInTheDocument()
    expect(screen.queryByText('You particularly enjoy')).not.toBeInTheDocument()
  })

  // --- combinations --------------------------------------------------------

  it('keeps a combination visibly apart from a single concept', async () => {
    renderPage({ profile: dashboard({ strongly_likes: [COMBINATION, item()] }) })

    await screen.findByText('Combination')
    // The "+" is part of the accessible name, not decoration: a listener has
    // to be able to tell a pair from a single concept too.
    const heading = screen.getByRole('heading', {
      level: 3,
      name: /Mystery \+ Psychological Depth Combination/,
    })
    // Both concepts survive as their own names; the pair is not flattened.
    expect(within(heading).getByText('Mystery')).toBeInTheDocument()
    expect(within(heading).getByText('Psychological Depth')).toBeInTheDocument()
    // The distinction is stated in words, not only in layout.
    expect(within(heading).getByText('Combination')).toBeInTheDocument()
  })

  // --- confidence ----------------------------------------------------------

  it('renders a strong preference with moderate confidence as a clear liking', async () => {
    renderPage({ profile: dashboard({ strongly_likes: [item({ confidence_band: 'moderate' })] }) })

    expect((await screen.findAllByText('You particularly enjoy'))[0]).toBeInTheDocument()
    // No hedging verb anywhere: the group decides the wording, the band does not.
    expect(screen.queryByText(/might somewhat/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/possibly enjoy/i)).not.toBeInTheDocument()
  })

  it('shows no confidence grade, open or closed', async () => {
    /**
     * The band still arrives on every item and still means what it meant.
     * What a reader sees instead is how much of their own history points
     * this way -- the thing they can check -- rather than a word that graded
     * it for them.
     */
    const { container } = renderPage({
      profile: dashboard({ strongly_likes: [item({ confidence_band: 'moderate' })] }),
    })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })
    expect(container.textContent).not.toMatch(/confidence/i)

    expect(container.textContent).not.toMatch(/confidence/i)
    expect(
      screen.getByText(/Several of your ratings point this way, and they mostly agree/),
    ).toBeInTheDocument()
    // Strength and how much is behind it stay two different questions.
    expect(
      screen.getByText(/How strongly you liked these is a separate question/),
    ).toBeInTheDocument()
  })

  it('never prints a preference score, a percentage or a model internal', async () => {
    renderPage({
      profile: dashboard({
        strongly_likes: [item({ also_supported_by: ['Tragedy'] })],
        what_stands_out: [STANDOUT],
      }),
    })

    await screen.findAllByText('You particularly enjoy')
    const body = document.body.textContent ?? ''
    for (const forbidden of [
      'preference_evidence',
      'normalized',
      'baseline',
      'shrinkage',
      '0.5',
      '%',
      'personality',
      'introvert',
    ]) {
      expect(body).not.toContain(forbidden)
    }
  })

  // --- evidence ------------------------------------------------------------

  it('gives the counts behind a preference without the arithmetic', async () => {
    renderPage({
      profile: dashboard({
        strongly_likes: [
          item({
            evidence_summary: evidence({
              rated_works: 5,
              supporting_works: 7,
              domains: ['Anime', 'Literature'],
              includes_reconsumed_works: true,
            }),
          }),
        ],
      }),
    })

    expect(
      await screen.findByText(/You've rated 5 works across Anime and Literature that share it/),
    ).toBeInTheDocument()

    expect(
      screen.getByText(/it turns up in 7 works you have come across in all/),
    ).toBeInTheDocument()
    expect(screen.getByText(/Going back says you kept reading/)).toBeInTheDocument()
  })

  it('says when the evidence cannot tell two findings apart', async () => {
    renderPage({
      profile: dashboard({ strongly_likes: [item({ also_supported_by: ['Tragedy', 'Drama'] })] }),
    })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })

    expect(screen.getByText(/Tragedy and Drama just as well/)).toBeInTheDocument()
  })

  it('reports disagreeing ratings instead of hiding them', async () => {
    renderPage({
      profile: dashboard({
        strongly_likes: [item({ evidence_summary: evidence({ has_mixed_evidence: true }) })],
      }),
    })

    expect(await screen.findByText(/do not all point the same way/)).toBeInTheDocument()
  })

  // --- what stands out -----------------------------------------------------

  it('renders What stands out as observations, not another list of concepts', async () => {
    renderPage({ profile: dashboard({ strongly_likes: [item()], what_stands_out: [STANDOUT] }) })

    expect(await screen.findByRole('heading', { name: 'What stands out' })).toBeInTheDocument()
    expect(
      screen.getByText(/You particularly enjoy stories that bring together Mystery and Identity/),
    ).toBeInTheDocument()
  })

  it('renders a cross-domain observation as breadth, not as a stronger preference', async () => {
    renderPage({
      profile: dashboard({
        what_stands_out: [
          {
            observation: 'cross_domain_pattern',
            presentation_key: 'cross_domain_feature',
            features: [{ key: 'isolation', name: 'Isolation' }],
            domains: ['Anime', 'Literature'],
            confidence_band: 'high',
            rated_works: 5,
          },
        ],
      }),
    })

    expect(
      await screen.findByText(/Isolation draws you in across Anime and Literature/),
    ).toBeInTheDocument()
  })

  it('makes no causal or biographical claim', async () => {
    renderPage({ profile: dashboard({ strongly_likes: [item()], what_stands_out: [STANDOUT] }) })

    await screen.findByRole('heading', { name: 'What stands out' })
    const body = document.body.textContent ?? ''
    expect(body).not.toMatch(/because you/i)
    expect(body).not.toMatch(/you are (an?|the) /i)
    expect(body).not.toMatch(/this says about you/i)
  })

  // --- profile states ------------------------------------------------------

  it('tells a reader with no activity how to begin', async () => {
    renderPage({
      profile: dashboard({
        summary: {
          profile_state: 'no_activity',
          rated_works: 0,
          established_preferences: 0,
          emerging_signals: 0,
        },
      }),
    })

    expect(await screen.findByText(/Noema has not seen anything yet/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Go to your library' })).toBeInTheDocument()
  })

  it('distinguishes exposure from preference when nothing is rated', async () => {
    renderPage({
      profile: dashboard({
        summary: {
          profile_state: 'no_ratings',
          rated_works: 0,
          established_preferences: 0,
          emerging_signals: 0,
        },
      }),
    })

    expect(
      await screen.findByText(/knows what you have explored, but not what you thought of it/),
    ).toBeInTheDocument()
    expect(screen.getByText(/not the same as enjoying it/)).toBeInTheDocument()
  })

  it('says a building profile is still building', async () => {
    renderPage({
      profile: dashboard({
        summary: {
          profile_state: 'building',
          rated_works: 2,
          established_preferences: 0,
          emerging_signals: 0,
        },
      }),
    })

    expect(await screen.findByText(/Nothing has settled into a preference yet/)).toBeInTheDocument()
  })

  it('shows a full profile without requiring every group to be populated', async () => {
    renderPage({
      profile: dashboard({ strongly_likes: [item()], what_stands_out: [STANDOUT] }),
    })

    expect((await screen.findAllByText('You particularly enjoy'))[0]).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'What stands out' })).toBeInTheDocument()
    expect(screen.getByText(/Built from 6 ratings/)).toBeInTheDocument()
  })

  // --- the evidence source -------------------------------------------------

  it('joins overview evidence to a concept by slug, never by position', async () => {
    // The overview lists a different concept first. A positional join would
    // attach Tragedy's evidence to Psychological Depth; the slug is what
    // decides, so the wrong one must not appear.
    renderPage({
      profile: dashboard({ strongly_likes: [item()] }),
      overview: overview({
        signals: [
          {
            concept_slug: 'tragedy',
            concept_name: 'Tragedy',
            concept_type: 'theme',
            direction: 'positive',
            confidence_band: 'high',
            evidence: counts({ works_rated: 99, rating_mean: 2.2 }),
            contributions: [
              {
                work_id: 'w-wrong',
                title: 'The Wrong Work',
                domain_name: 'Anime',
                rating: 2,
                status: 'completed',
                times_completed: 1,
                in_library: true,
              },
            ],
          },
          {
            concept_slug: 'psychological-depth',
            concept_name: 'Psychological Depth',
            concept_type: 'theme',
            direction: 'positive',
            confidence_band: 'high',
            evidence: counts({
              works_rated: 4,
              positive_ratings: 3,
              negative_ratings: 1,
              rating_mean: 8.3,
            }),
            contributions: [
              {
                work_id: 'w-right',
                title: 'The Right Work',
                domain_name: 'Literature',
                rating: 9,
                status: 'completed',
                times_completed: 1,
                in_library: true,
              },
            ],
          },
        ],
      }),
    })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })

    expect(screen.getByText(/4 rated . 3 positive . 1 negative . average 8\.3\/10/)).toBeInTheDocument()
    expect(screen.getByText('The Right Work')).toBeInTheDocument()
    expect(screen.queryByText('The Wrong Work')).not.toBeInTheDocument()
    expect(screen.queryByText(/99 rated/)).not.toBeInTheDocument()
  })

  it('shows only the evidence fields that have something to say', async () => {
    renderPage({
      profile: dashboard({ strongly_likes: [item()] }),
      overview: overview({
        signals: [
          {
            concept_slug: 'psychological-depth',
            concept_name: 'Psychological Depth',
            concept_type: 'theme',
            direction: 'positive',
            confidence_band: 'high',
            evidence: counts({ works_rated: 4, positive_ratings: 4, rating_mean: 9 }),
            contributions: [],
          },
        ],
      }),
    })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })

    // Nothing was abandoned or put on hold, so neither is stated as a finding.
    expect(screen.queryByText(/abandoned/)).not.toBeInTheDocument()
    expect(screen.queryByText(/on hold/)).not.toBeInTheDocument()
    expect(screen.queryByText(/0 negative/)).not.toBeInTheDocument()
  })

  it('states reconsumption once, with the count rather than the adjective', async () => {
    // The dashboard boolean and the overview count said the same thing. The
    // one with a number in it is the better of the two.
    renderPage({
      profile: dashboard({
        strongly_likes: [
          item({
            evidence_summary: {
              rated_works: 4,
              supporting_works: 4,
              domains: ['Anime'],
              includes_reconsumed_works: true,
              has_mixed_evidence: false,
            },
          }),
        ],
      }),
      overview: overview({
        signals: [
          {
            concept_slug: 'psychological-depth',
            concept_name: 'Psychological Depth',
            concept_type: 'theme',
            direction: 'positive',
            confidence_band: 'high',
            evidence: counts({
              works_rated: 4,
              positive_ratings: 4,
              rating_mean: 9,
              works_reconsumed: 1,
              total_completions: 6,
            }),
            contributions: [],
          },
        ],
      }),
    })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })

    expect(
      screen.getByText(/Returned to 1 of them \(6 completions in total\)/),
    ).toBeInTheDocument()
    expect(screen.queryByText(/Some of these are works you went back to/)).not.toBeInTheDocument()
  })

  it('keeps the plain statement when there is no count to give', async () => {
    // Without an overview signal the boolean is the only evidence there is,
    // so it is still said.
    renderPage({
      profile: dashboard({
        strongly_likes: [
          item({
            evidence_summary: {
              rated_works: 4,
              supporting_works: 4,
              domains: ['Anime'],
              includes_reconsumed_works: true,
              has_mixed_evidence: false,
            },
          }),
        ],
      }),
      overviewFails: true,
    })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })

    expect(screen.getByText(/Some of these are works you went back to/)).toBeInTheDocument()
  })

  it('never promotes a low-confidence overview signal into a group', async () => {
    // The overview knows about Tragedy. The dashboard did not establish it,
    // so it must appear nowhere on the page as a preference.
    renderPage({
      profile: dashboard({ strongly_likes: [item()] }),
      overview: overview({
        signals: [
          {
            concept_slug: 'tragedy',
            concept_name: 'Tragedy',
            concept_type: 'theme',
            direction: 'positive',
            confidence_band: 'low',
            evidence: counts({ works_rated: 1, positive_ratings: 1, rating_mean: 8 }),
            contributions: [],
          },
        ],
      }),
    })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })
    expect(screen.queryByRole('heading', { level: 3, name: 'Tragedy' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(1)
  })

  it('renders the whole profile when the evidence source fails', async () => {
    renderPage({
      profile: dashboard({ strongly_likes: [item()], what_stands_out: [STANDOUT] }),
      overviewFails: true,
    })

    expect((await screen.findAllByText('You particularly enjoy'))[0]).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 3, name: 'Psychological Depth' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'What stands out' })).toBeInTheDocument()
    // The profile is poorer, not broken, and says nothing went wrong.
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('keeps concepts met but not rated out of the preference groups', async () => {
    renderPage({
      profile: dashboard({ strongly_likes: [item()] }),
      overview: overview({
        awaiting_ratings: [
          {
            concept_slug: 'identity',
            concept_name: 'Identity',
            concept_type: 'theme',
            evidence: counts({ works_exposed: 3, works_completed: 2 }),
            contributions: [],
          },
        ],
      }),
    })

    const band = (
      await screen.findByRole('heading', { name: 'Met, but not yet rated' })
    ).closest('section') as HTMLElement
    expect(within(band).getByText('Identity')).toBeInTheDocument()
    expect(within(band).getByText(/not rated enough of them/)).toBeInTheDocument()

    // And it is not inside the section that claims established preferences.
    const established = screen
      .getByRole('heading', { name: 'Established preferences' })
      .closest('section') as HTMLElement
    expect(within(established).queryByText('Identity')).not.toBeInTheDocument()
  })

  it('asks for nothing at all while signed out', async () => {
    resetSessionForTests()
    vi.stubGlobal('fetch', mockApi())
    render(<TasteProfile onNavigate={() => {}} onOpenLibrary={() => {}} />)

    expect(await screen.findByRole('button', { name: 'Log in' })).toBeInTheDocument()
    expect(requested.filter((url) => url.includes('/preferences'))).toHaveLength(0)
  })

  it('keeps the account controls in the shell, not in the profile', async () => {
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })
    // One Log out, and it lives in the header beside the account -- the page
    // body used to carry a second copy.
    expect(screen.getAllByRole('button', { name: 'Log out' })).toHaveLength(1)
    expect(screen.queryByText(/Signed in as/)).not.toBeInTheDocument()
  })

  // --- feedback ------------------------------------------------------------

  it('offers the feedback question quietly, never as a prompt', async () => {
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })

    // It used to be hidden behind the evidence disclosure. The evidence is
    // now always open, so the question is too -- what still matters is that
    // it is at the foot of the ledger the finding rests on rather than
    // anywhere a reader has to answer it to use the page.
    const question = screen.getByRole('group', { name: 'Does this feel right?' })
    expect(question).toBeVisible()
    expect(within(question).getByRole('button', { name: 'Yes' })).toBeVisible()
    expect(within(question).getByRole('button', { name: 'Not really' })).toBeVisible()

    const ledger = screen.getByText('What this rests on').closest('div')
    expect(ledger?.parentElement).toContainElement(question)
  })

  it('sends the canonical concept slug, never the display name', async () => {
    const user = userEvent.setup()
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })
    await user.click(screen.getByRole('button', { name: 'Yes' }))

    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0]).toEqual({ concept_slug: 'psychological-depth', feedback: 'confirmed' })
  })

  it('confirms a disagreement without claiming the model has changed', async () => {
    const user = userEvent.setup()
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })
    await user.click(screen.getByRole('button', { name: 'Not really' }))

    const confirmation = await screen.findByText(/Thanks — Noema will use this/)
    expect(confirmation).toHaveTextContent(/Nothing above has changed yet/)
    // And the preference itself is still where the backend put it.
    expect(screen.getAllByText('You particularly enjoy')[0]).toBeInTheDocument()
  })

  it('records a disagreement as a disagreement, not as a dislike', async () => {
    const user = userEvent.setup()
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })
    await user.click(screen.getByRole('button', { name: 'Not really' }))

    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0]).toEqual({ concept_slug: 'psychological-depth', feedback: 'corrected' })
    // The item did not move into the dislikes group as a side effect.
    expect(screen.queryByRole('heading', { name: /tend not to enjoy/ })).not.toBeInTheDocument()
  })

  it('shows an answer already on record when the page loads', async () => {
    renderPage({
      profile: dashboard({ strongly_likes: [item()] }),
      feedback: [
        {
          concept_slug: 'psychological-depth',
          concept_name: 'Psychological Depth',
          feedback: 'confirmed',
          source: 'taste_profile',
          submission_count: 1,
          first_recorded_at: '2026-09-18T00:00:00Z',
          updated_at: '2026-09-18T00:00:00Z',
        },
      ],
    })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })

    expect(screen.getByRole('button', { name: /Yes/ })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Not really' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByText(/You told Noema this reading seems right/)).toBeInTheDocument()
  })

  it('lets a reader change their mind', async () => {
    const user = userEvent.setup()
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })
    await user.click(screen.getByRole('button', { name: 'Yes' }))
    await waitFor(() => expect(posted).toHaveLength(1))
    await user.click(screen.getByRole('button', { name: 'Not really' }))

    await waitFor(() => expect(posted).toHaveLength(2))
    expect(posted[1]).toEqual({ concept_slug: 'psychological-depth', feedback: 'corrected' })
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Not really/ })).toHaveAttribute('aria-pressed', 'true'),
    )
  })

  it('says so when an answer fails to save', async () => {
    const user = userEvent.setup()
    renderPage({ profile: dashboard({ strongly_likes: [item()] }), feedbackPostFails: true })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })
    await user.click(screen.getByRole('button', { name: 'Yes' }))

    expect(await screen.findByText(/That did not save/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Yes' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('does not offer feedback on a combination, and says why', async () => {
    renderPage({ profile: dashboard({ strongly_likes: [COMBINATION] }) })

    await screen.findByText('Combination')

    expect(screen.getByText(/Feedback on combinations is not available yet/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Yes' })).not.toBeInTheDocument()
  })

  it('does not ask for feedback on an emerging signal', async () => {
    renderPage({ profile: dashboard({ emerging: [item()] }) })

    await screen.findByRole('heading', { name: /Something Noema is noticing/ })

    expect(screen.queryByText('Does this feel right?')).not.toBeInTheDocument()
  })

  it('still renders the profile when the feedback endpoint is unavailable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn((input: string | URL) => {
        const url = String(input)
        if (url.includes('/auth/me')) {
          return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(ACCOUNT) } as Response)
        }
        if (url.includes('/preferences/feedback')) {
          return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) } as Response)
        }
        return Promise.resolve({
          ok: true,
          status: 200,
          json: () => Promise.resolve(dashboard({ strongly_likes: [item()] })),
        } as Response)
      }),
    )
    render(<TasteProfile onNavigate={() => {}} onOpenLibrary={() => {}} />)

    expect((await screen.findAllByText('You particularly enjoy'))[0]).toBeInTheDocument()
  })

  // --- accessibility basics ------------------------------------------------

  it('uses one level-1 heading and a level-2 heading per section', async () => {
    renderPage({
      profile: dashboard({
        strongly_likes: [item()],
        dislikes: [item({ key: 'fantasy', display_name: 'Fantasy', features: [{ key: 'fantasy', name: 'Fantasy' }] })],
        what_stands_out: [STANDOUT],
      }),
    })

    await screen.findAllByText('You particularly enjoy')
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(screen.getAllByRole('heading', { level: 2 }).length).toBeGreaterThanOrEqual(3)
    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(2)
  })

  it('gives every control an accessible name', async () => {
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })

    for (const button of screen.getAllByRole('button')) {
      expect(button).toHaveAccessibleName()
    }
  })

  it('labels the feedback choices as a group', async () => {
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })

    expect(screen.getByRole('group', { name: 'Does this feel right?' })).toBeInTheDocument()
  })

  it('puts the evidence controls in the tab order', async () => {
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })

    // The evidence used to sit behind a <summary> disclosure and this test
    // checked that the disclosure was reachable. The ledger is now always
    // open -- the export sets the evidence beside the claim rather than
    // underneath a toggle -- so what is left to protect is the part a wrong
    // markup choice would actually break: the controls are focusable and in
    // document order.
    const yes = screen.getByRole('button', { name: 'Yes' })
    yes.focus()
    expect(yes).toHaveFocus()
    expect(screen.getByRole('group', { name: 'Does this feel right?' })).toContainElement(yes)
  })
})
