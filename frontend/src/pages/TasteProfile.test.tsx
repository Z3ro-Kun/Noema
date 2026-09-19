import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TasteProfile from './TasteProfile'
import { setSessionToken } from '../api/library'
import type {
  PreferenceFeedback,
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

interface MockOptions {
  profile?: TasteDashboard
  feedback?: PreferenceFeedback[]
  dashboardFails?: boolean
  feedbackPostFails?: boolean
  /** Left unresolved, so the loading state can be observed. */
  hangDashboard?: boolean
}

let posted: unknown[] = []

function mockApi(options: MockOptions = {}) {
  posted = []
  return vi.fn((input: string | URL, init?: RequestInit) => {
    const url = String(input)
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
    setSessionToken('a-session-token')
  })

  // --- the page itself -----------------------------------------------------

  it('names the page for the reader, not for the model', async () => {
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Your Taste' }),
    ).toBeInTheDocument()
    expect(screen.getByText(/works you have rated/)).toBeInTheDocument()
  })

  it('shows a loading state while the profile is being derived', async () => {
    renderPage({ hangDashboard: true })

    // The session resolves first; the profile request is the one left hanging.
    expect(await screen.findByText(/Reading your ratings/)).toBeInTheDocument()
  })

  it('reports a failure instead of rendering an empty profile', async () => {
    renderPage({ dashboardFails: true })

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('the engine is unavailable')
    expect(screen.queryByText('You particularly enjoy')).not.toBeInTheDocument()
  })

  // --- the four groups -----------------------------------------------------

  it('renders each group under its own heading', async () => {
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

    expect(await screen.findByRole('heading', { name: /You particularly enjoy/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /You also enjoy, more mildly/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /You tend not to enjoy/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Noema is beginning to notice/ })).toBeInTheDocument()

    expect(screen.getByRole('heading', { level: 3, name: 'Psychological Depth' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 3, name: 'Tragedy' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 3, name: 'Fantasy' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 3, name: 'Identity' })).toBeInTheDocument()
  })

  it('omits a group with nothing in it rather than showing an empty block', async () => {
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    await screen.findByRole('heading', { name: /You particularly enjoy/ })
    expect(screen.queryByRole('heading', { name: /You also enjoy/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /tend not to enjoy/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /beginning to notice/ })).not.toBeInTheDocument()
  })

  it('says a mild preference is mild, not uncertain', async () => {
    renderPage({ profile: dashboard({ mildly_likes: [item({ confidence_band: 'high' })] }) })

    const meaning = await screen.findByText(/Positive, just less pronounced/)
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

    expect(await screen.findByText(/Too early to call these preferences/)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: /You particularly enjoy/ })).not.toBeInTheDocument()
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

    expect(await screen.findByRole('heading', { name: /You particularly enjoy/ })).toBeInTheDocument()
    // No hedging verb anywhere: the group decides the wording, the band does not.
    expect(screen.queryByText(/might somewhat/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/possibly enjoy/i)).not.toBeInTheDocument()
  })

  it('keeps confidence out of the primary hierarchy and inside the details', async () => {
    const user = userEvent.setup()
    renderPage({ profile: dashboard({ strongly_likes: [item({ confidence_band: 'moderate' })] }) })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })
    expect(screen.queryByText(/Moderate confidence/)).not.toBeVisible()

    await user.click(screen.getByText(/Why does Noema think this/))
    expect(screen.getByText(/Moderate confidence/)).toBeVisible()
    expect(
      screen.getByText(/how much evidence there is, not how much you liked it/),
    ).toBeInTheDocument()
  })

  it('never prints a preference score, a percentage or a model internal', async () => {
    renderPage({
      profile: dashboard({
        strongly_likes: [item({ also_supported_by: ['Tragedy'] })],
        what_stands_out: [STANDOUT],
      }),
    })

    await screen.findByRole('heading', { name: /You particularly enjoy/ })
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
    const user = userEvent.setup()
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

    expect(await screen.findByText(/Based on 5 rated works across Anime and Literature/)).toBeInTheDocument()

    await user.click(screen.getByText(/Why does Noema think this/))
    expect(screen.getByText(/7 works are associated with it/)).toBeInTheDocument()
    expect(screen.getByText(/Repetition is context, not a rating/)).toBeInTheDocument()
  })

  it('says when the evidence cannot tell two findings apart', async () => {
    const user = userEvent.setup()
    renderPage({
      profile: dashboard({ strongly_likes: [item({ also_supported_by: ['Tragedy', 'Drama'] })] }),
    })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })
    await user.click(screen.getByText(/Why does Noema think this/))

    expect(screen.getByText(/Tragedy and Drama just as well/)).toBeInTheDocument()
  })

  it('reports disagreeing ratings instead of hiding them', async () => {
    renderPage({
      profile: dashboard({
        strongly_likes: [item({ evidence_summary: evidence({ has_mixed_evidence: true }) })],
      }),
    })

    expect(await screen.findByText(/do not all agree/)).toBeInTheDocument()
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

    expect(await screen.findByRole('heading', { name: /You particularly enjoy/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'What stands out' })).toBeInTheDocument()
    expect(screen.getByText(/Built from 6 rated works/)).toBeInTheDocument()
  })

  // --- feedback ------------------------------------------------------------

  it('offers the feedback question from inside the details, never as a prompt', async () => {
    const user = userEvent.setup()
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })
    expect(screen.queryByRole('button', { name: 'Yes' })).not.toBeVisible()

    await user.click(screen.getByText(/Why does Noema think this/))
    expect(screen.getByText('Does this feel right?')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Yes' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Not really' })).toBeVisible()
  })

  it('sends the canonical concept slug, never the display name', async () => {
    const user = userEvent.setup()
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })
    await user.click(screen.getByText(/Why does Noema think this/))
    await user.click(screen.getByRole('button', { name: 'Yes' }))

    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0]).toEqual({ concept_slug: 'psychological-depth', feedback: 'confirmed' })
  })

  it('confirms a disagreement without claiming the model has changed', async () => {
    const user = userEvent.setup()
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })
    await user.click(screen.getByText(/Why does Noema think this/))
    await user.click(screen.getByRole('button', { name: 'Not really' }))

    const confirmation = await screen.findByText(/Thanks — Noema will use this/)
    expect(confirmation).toHaveTextContent(/Nothing above has changed yet/)
    // And the preference itself is still where the backend put it.
    expect(screen.getByRole('heading', { name: /You particularly enjoy/ })).toBeInTheDocument()
  })

  it('records a disagreement as a disagreement, not as a dislike', async () => {
    const user = userEvent.setup()
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })
    await user.click(screen.getByText(/Why does Noema think this/))
    await user.click(screen.getByRole('button', { name: 'Not really' }))

    await waitFor(() => expect(posted).toHaveLength(1))
    expect(posted[0]).toEqual({ concept_slug: 'psychological-depth', feedback: 'corrected' })
    // The item did not move into the dislikes group as a side effect.
    expect(screen.queryByRole('heading', { name: /tend not to enjoy/ })).not.toBeInTheDocument()
  })

  it('shows an answer already on record when the page loads', async () => {
    const user = userEvent.setup()
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
    await user.click(screen.getByText(/Why does Noema think this/))

    expect(screen.getByRole('button', { name: /Yes/ })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: 'Not really' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByText(/You told Noema this reading seems right/)).toBeInTheDocument()
  })

  it('lets a reader change their mind', async () => {
    const user = userEvent.setup()
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })
    await user.click(screen.getByText(/Why does Noema think this/))
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
    await user.click(screen.getByText(/Why does Noema think this/))
    await user.click(screen.getByRole('button', { name: 'Yes' }))

    expect(await screen.findByText(/That did not save/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Yes' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('does not offer feedback on a combination, and says why', async () => {
    const user = userEvent.setup()
    renderPage({ profile: dashboard({ strongly_likes: [COMBINATION] }) })

    await screen.findByText('Combination')
    await user.click(screen.getByText(/Why does Noema think this/))

    expect(screen.getByText(/Feedback on combinations is not available yet/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Yes' })).not.toBeInTheDocument()
  })

  it('does not ask for feedback on an emerging signal', async () => {
    const user = userEvent.setup()
    renderPage({ profile: dashboard({ emerging: [item()] }) })

    await screen.findByRole('heading', { name: /Noema is beginning to notice/ })
    await user.click(screen.getByText(/Why does Noema think this/))

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

    expect(await screen.findByRole('heading', { name: /You particularly enjoy/ })).toBeInTheDocument()
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

    await screen.findByRole('heading', { name: /You particularly enjoy/ })
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(screen.getAllByRole('heading', { level: 2 }).length).toBeGreaterThanOrEqual(3)
    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(2)
  })

  it('gives every control an accessible name', async () => {
    const user = userEvent.setup()
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })
    await user.click(screen.getByText(/Why does Noema think this/))

    for (const button of screen.getAllByRole('button')) {
      expect(button).toHaveAccessibleName()
    }
  })

  it('labels the feedback choices as a group', async () => {
    const user = userEvent.setup()
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })
    await user.click(screen.getByText(/Why does Noema think this/))

    expect(screen.getByRole('group', { name: 'Does this feel right?' })).toBeInTheDocument()
  })

  it('puts the details panel and its controls in the tab order', async () => {
    const user = userEvent.setup()
    renderPage({ profile: dashboard({ strongly_likes: [item()] }) })

    await screen.findByRole('heading', { level: 3, name: 'Psychological Depth' })
    const summary = screen.getByText(/Why does Noema think this/)

    // A native <summary> is focusable and toggles on Enter in a browser.
    // jsdom does not implement that default action, so what is asserted here
    // is reachability -- the part a wrong markup choice would actually break.
    summary.focus()
    expect(summary).toHaveFocus()

    await user.click(summary)
    await user.tab()
    expect(screen.getByRole('button', { name: 'Yes' })).toHaveFocus()
  })
})
