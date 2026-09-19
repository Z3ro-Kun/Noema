import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Preferences from './Preferences'
import { setSessionToken } from '../api/client'
import { resetSessionForTests, setAuthenticatedForTests } from '../auth/session'
import type {
  ConfidenceBand,
  ContributingWork,
  EvidenceCounts,
  PreferenceDirection,
  PreferenceOverview,
} from '../types/api'

/**
 * The preference page's contract with its reader.
 *
 * Most of these check wording rather than layout, because wording is what
 * this phase is actually about: the page must report observed media
 * preference evidence and must never turn it into a claim about the person.
 *
 * The fixtures mirror the shapes the Phase 1N/1O evaluation cases produce, so
 * what is asserted here is what a real profile would render.
 */

const USER = {
  id: 'user-1',
  email: 'reader@example.test',
  display_name: null,
  created_at: '2026-09-18T00:00:00Z',
}

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

function work(overrides: Partial<ContributingWork> = {}): ContributingWork {
  return {
    work_id: crypto.randomUUID(),
    title: 'MONSTER',
    domain_name: 'Anime',
    rating: 9,
    status: 'completed',
    times_completed: 1,
    in_library: true,
    ...overrides,
  }
}

function signal(
  name: string,
  direction: PreferenceDirection,
  band: ConfidenceBand,
  evidence: EvidenceCounts,
  contributions: ContributingWork[] = [work()],
) {
  return {
    concept_slug: name.toLowerCase().replace(/\s+/g, '-'),
    concept_name: name,
    concept_type: 'theme' as const,
    direction,
    confidence_band: band,
    evidence,
    contributions,
  }
}

function overview(partial: Partial<PreferenceOverview> = {}): PreferenceOverview {
  return {
    summary: {
      total_interactions: 5,
      works_rated: 5,
      signals_with_direction: 0,
      concepts_awaiting_ratings: 0,
      rating_context_established: true,
      interactions_without_concepts: 0,
      ...partial.summary,
    },
    signals: partial.signals ?? [],
    awaiting_ratings: partial.awaiting_ratings ?? [],
  }
}

/** Case A's shape: five psychological works, rated 8-10. */
const CASE_A = overview({
  summary: {
    total_interactions: 5,
    works_rated: 5,
    signals_with_direction: 1,
    concepts_awaiting_ratings: 0,
    rating_context_established: true,
    interactions_without_concepts: 0,
  },
  signals: [
    signal(
      'Psychological Depth',
      'positive',
      'moderate',
      counts({
        works_exposed: 5,
        works_started: 5,
        works_completed: 5,
        works_rated: 5,
        positive_ratings: 5,
        rating_mean: 9,
        total_completions: 5,
      }),
      [
        work({ title: 'serial experiments lain', rating: 10 }),
        work({ title: 'MONSTER', rating: 9 }),
        work({ title: 'Metamorphosis', rating: 9, domain_name: 'Literature' }),
      ],
    ),
  ],
})

/** Case B's shape: the same works, rated 3-5. */
const CASE_B = overview({
  summary: {
    total_interactions: 5,
    works_rated: 5,
    signals_with_direction: 1,
    concepts_awaiting_ratings: 0,
    rating_context_established: true,
    interactions_without_concepts: 0,
  },
  signals: [
    signal(
      'Psychological Depth',
      'negative',
      'moderate',
      counts({
        works_exposed: 5,
        works_started: 5,
        works_completed: 5,
        works_rated: 5,
        negative_ratings: 5,
        rating_mean: 4.2,
        total_completions: 5,
      }),
      [work({ title: 'MONSTER', rating: 5 }), work({ title: 'DEATH NOTE', rating: 4 })],
    ),
  ],
})

function mockApi(body: PreferenceOverview | null, status = 200) {
  return vi.fn((input: string | URL) => {
    const url = String(input)
    const json = (payload: unknown, code = 200) =>
      Promise.resolve({
        ok: code < 400,
        status: code,
        json: () => Promise.resolve(payload),
      } as Response)

    if (url.includes('/auth/me')) return json(USER)
    if (url.includes('/auth/logout')) return Promise.resolve({ ok: true, status: 204 } as Response)
    if (url.includes('/preferences/overview')) {
      return body === null ? json({ detail: 'nope' }, status) : json(body)
    }
    return json({})
  })
}

async function renderSignedIn(body: PreferenceOverview | null, status = 200) {
  setAuthenticatedForTests(USER.email)
  vi.stubGlobal('fetch', mockApi(body, status))
  render(<Preferences onBack={() => {}} onSignIn={() => {}} />)
  if (body) await screen.findByText(/Noema builds these signals/)
}

describe('Preferences page', () => {
  beforeEach(() => {
    resetSessionForTests()
    setSessionToken(null)
    vi.unstubAllGlobals()
  })

  // --- authentication ------------------------------------------------------

  it('asks for credentials before showing any evidence', async () => {
    vi.stubGlobal('fetch', mockApi(CASE_A))

    render(<Preferences onBack={() => {}} onSignIn={() => {}} />)

    // A prompt pointing at the Login page, not a second login form.
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Email')).not.toBeInTheDocument()
    expect(screen.queryByText('Psychological Depth')).not.toBeInTheDocument()
  })

  it('sends the session token and never a user identifier', async () => {
    setAuthenticatedForTests(USER.email)
    const fetchMock = mockApi(CASE_A)
    vi.stubGlobal('fetch', fetchMock)

    render(<Preferences onBack={() => {}} onSignIn={() => {}} />)
    await screen.findByText('Psychological Depth')

    const call = fetchMock.mock.calls.find(([url]) =>
      String(url).includes('/preferences/overview'),
    )
    expect(call).toBeDefined()
    expect(String(call?.[0])).not.toMatch(/user_id|user=/)
  })

  // --- direction -----------------------------------------------------------

  it('renders a positive signal as evidence, not as a verdict', async () => {
    await renderSignedIn(CASE_A)

    expect(await screen.findByText('Positive preference evidence')).toBeInTheDocument()
    expect(screen.getByText('Psychological Depth')).toBeInTheDocument()
  })

  it('renders a negative signal for the same exposure with lower ratings', async () => {
    await renderSignedIn(CASE_B)

    expect(await screen.findByText('Negative preference evidence')).toBeInTheDocument()
    expect(screen.queryByText('Positive preference evidence')).not.toBeInTheDocument()
  })

  it('renders mixed evidence without calling the reader inconsistent', async () => {
    await renderSignedIn(
      overview({
        summary: {
          total_interactions: 4,
          works_rated: 4,
          signals_with_direction: 1,
          concepts_awaiting_ratings: 0,
          rating_context_established: false,
          interactions_without_concepts: 0,
        },
        signals: [
          signal(
            'Adventure',
            'neutral',
            'moderate',
            counts({ works_exposed: 4, works_completed: 4, works_rated: 4, rating_mean: 6 }),
          ),
        ],
      }),
    )

    expect(await screen.findByText('Mixed evidence, no clear direction')).toBeInTheDocument()
    expect(document.body.textContent).not.toMatch(/inconsistent/i)
  })

  it('does not convey direction by colour alone', async () => {
    await renderSignedIn(CASE_A)

    const badge = (await screen.findByText('Positive preference evidence')).closest('span')
    // A text marker sits beside the label for anyone who cannot see the hue.
    expect(badge?.textContent).toContain('+')
  })

  // --- confidence ----------------------------------------------------------

  it('labels confidence separately from direction', async () => {
    await renderSignedIn(CASE_A)

    expect(await screen.findByText('Confidence: Moderate')).toBeInTheDocument()
  })

  it('never renders a preference score or a percentage', async () => {
    await renderSignedIn(CASE_A)
    await screen.findByText('Psychological Depth')

    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/\d+%/)
    expect(text).not.toMatch(/0\.\d\d/)
  })

  it('separates weakly supported signals from well supported ones', async () => {
    await renderSignedIn(
      overview({
        signals: [
          signal(
            'Psychological Depth',
            'positive',
            'moderate',
            counts({ works_exposed: 5, works_completed: 5, works_rated: 5, rating_mean: 9 }),
          ),
          signal(
            'Time Manipulation',
            'positive',
            'low',
            counts({ works_exposed: 1, works_completed: 1, works_rated: 1, rating_mean: 10 }),
          ),
        ],
      }),
    )

    expect(await screen.findByText('Preference signals')).toBeInTheDocument()
    expect(screen.getByText('Early signals, from one or two ratings')).toBeInTheDocument()
  })

  // --- ordering (Phase 1R) --------------------------------------------------

  it('renders signals in the order the server sent them', async () => {
    // The page sorts nothing. If it ever did, the backend's measured
    // ordering would be silently overridden here.
    await renderSignedIn(
      overview({
        signals: [
          signal('Science Fiction', 'positive', 'moderate', counts({ works_rated: 4 })),
          signal('Coming Of Age', 'negative', 'moderate', counts({ works_rated: 2 })),
          signal('Tragedy', 'positive', 'moderate', counts({ works_rated: 3 })),
        ],
      }),
    )

    await screen.findByText('Science Fiction')
    const headings = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent)
    expect(headings).toEqual(['Science Fiction', 'Coming Of Age', 'Tragedy'])
  })

  it('says what the ordering means, and what it does not mean', async () => {
    await renderSignedIn(CASE_A)

    expect(
      await screen.findByText(/most supporting evidence first/i),
    ).toBeInTheDocument()
    const text = (document.body.textContent ?? '').toLowerCase()
    expect(text).toContain('not how strongly')
  })

  it('never describes the ordering as strength or importance', async () => {
    await renderSignedIn(CASE_A)
    const user = userEvent.setup()
    await user.click(await screen.findByText('How this works'))

    const text = (document.body.textContent ?? '').toLowerCase()
    for (const forbidden of [
      'your strongest',
      'strongest preference',
      'most important',
      'top preference',
      'ranked by',
      'preference score',
    ]) {
      expect(text).not.toContain(forbidden)
    }
  })

  it('does not number the signals, so position cannot read as a score', async () => {
    await renderSignedIn(
      overview({
        signals: [
          signal('Science Fiction', 'positive', 'moderate', counts({ works_rated: 4 })),
          signal('Tragedy', 'positive', 'moderate', counts({ works_rated: 3 })),
        ],
      }),
    )

    const heading = await screen.findByText('Science Fiction')
    expect(heading.textContent).toBe('Science Fiction')
    const card = heading.closest('article')
    expect(card?.textContent).not.toMatch(/^\s*1[.)]/)
  })

  it('leads a negative signal when that is what the evidence supports', async () => {
    // Confidence ordering can put a well-evidenced dislike first. The page
    // must present it as evidence, with the same shape as any other signal.
    await renderSignedIn(
      overview({
        signals: [
          signal(
            'Coming Of Age',
            'negative',
            'moderate',
            counts({ works_rated: 2, negative_ratings: 2, rating_mean: 2.5 }),
          ),
          signal('Science Fiction', 'positive', 'moderate', counts({ works_rated: 5 })),
        ],
      }),
    )

    const first = await screen.findByText('Coming Of Age')
    const headings = screen.getAllByRole('heading', { level: 3 }).map((h) => h.textContent)
    expect(headings[0]).toBe('Coming Of Age')
    const card = first.closest('article')
    expect(card?.textContent).toContain('Negative preference evidence')
    expect(card?.textContent).toContain('Confidence: Moderate')
  })

  // --- evidence and attribution -------------------------------------------

  it('shows the counts a reader can check the claim against', async () => {
    await renderSignedIn(CASE_A)

    expect(await screen.findByText(/5 rated · average 9\/10 · 5 completed/)).toBeInTheDocument()
  })

  it('lists the contributing works with the ratings given', async () => {
    await renderSignedIn(CASE_A)
    const user = userEvent.setup()

    await user.click(await screen.findByText(/Contributing works \(3\)/))

    expect(screen.getByText('serial experiments lain')).toBeInTheDocument()
    expect(screen.getByText('10/10')).toBeInTheDocument()
    // Cross-domain contributions are visible as such.
    expect(screen.getByText('Literature')).toBeInTheDocument()
  })

  it('shows an unrated contributing work as not rated rather than as zero', async () => {
    await renderSignedIn(
      overview({
        signals: [
          signal(
            'Adventure',
            'positive',
            'low',
            counts({ works_exposed: 2, works_completed: 2, works_rated: 1, rating_mean: 9 }),
            [work({ title: 'Rated One', rating: 9 }), work({ title: 'Unrated One', rating: null })],
          ),
        ],
      }),
    )
    const user = userEvent.setup()

    await user.click(await screen.findByText(/Contributing works/))

    expect(screen.getByText('not rated')).toBeInTheDocument()
    expect(screen.queryByText('0/10')).not.toBeInTheDocument()
  })

  it('marks a work that has left the library', async () => {
    await renderSignedIn(
      overview({
        signals: [
          signal(
            'Horror',
            'positive',
            'low',
            counts({ works_exposed: 1, works_completed: 1, works_rated: 1, rating_mean: 10 }),
            [work({ title: 'Frankenstein', rating: 10, in_library: false })],
          ),
        ],
      }),
    )
    const user = userEvent.setup()

    await user.click(await screen.findByText(/Contributing works/))

    expect(screen.getByText('removed from library')).toBeInTheDocument()
  })

  // --- unrated exposure ----------------------------------------------------

  it('keeps unrated exposure out of the preference signals entirely', async () => {
    await renderSignedIn(
      overview({
        summary: {
          total_interactions: 3,
          works_rated: 0,
          signals_with_direction: 0,
          concepts_awaiting_ratings: 1,
          rating_context_established: false,
          interactions_without_concepts: 0,
        },
        awaiting_ratings: [
          {
            concept_slug: 'crime-and-investigation',
            concept_name: 'Crime and Investigation',
            concept_type: 'motif',
            evidence: counts({
              works_exposed: 3,
              works_started: 3,
              works_completed: 3,
              total_completions: 3,
            }),
            contributions: [work({ rating: null })],
          },
        ],
      }),
    )

    expect(await screen.findByText('Watched or read, but not rated')).toBeInTheDocument()
    expect(screen.getByText('No preference direction yet')).toBeInTheDocument()
    expect(screen.queryByText('Positive preference evidence')).not.toBeInTheDocument()
    expect(screen.queryByText('Preference signals')).not.toBeInTheDocument()
  })

  // --- behaviour that is not preference ------------------------------------

  it('shows abandonment as behaviour, not as a negative preference', async () => {
    await renderSignedIn(
      overview({
        summary: {
          total_interactions: 2,
          works_rated: 0,
          signals_with_direction: 0,
          concepts_awaiting_ratings: 1,
          rating_context_established: false,
          interactions_without_concepts: 0,
        },
        awaiting_ratings: [
          {
            concept_slug: 'war',
            concept_name: 'War',
            concept_type: 'motif',
            evidence: counts({
              works_exposed: 2,
              works_started: 2,
              works_abandoned: 1,
              works_on_hold: 1,
            }),
            contributions: [work({ rating: null, status: 'abandoned' })],
          },
        ],
      }),
    )

    const card = (await screen.findByText('War')).closest('article')
    expect(within(card!).getByText(/1 abandoned/)).toBeInTheDocument()
    // on_hold stays its own thing, and neither becomes a direction.
    expect(within(card!).getByText(/1 on hold/)).toBeInTheDocument()
    expect(within(card!).queryByText('Negative preference evidence')).not.toBeInTheDocument()
  })

  it('shows reconsumption as behaviour, separate from the rating', async () => {
    await renderSignedIn(
      overview({
        signals: [
          signal(
            'Crime and Investigation',
            'positive',
            'moderate',
            counts({
              works_exposed: 2,
              works_completed: 2,
              works_rated: 2,
              rating_mean: 9,
              works_reconsumed: 1,
              total_completions: 4,
            }),
          ),
        ],
      }),
    )

    const card = (await screen.findByText('Crime and Investigation')).closest('article')
    expect(within(card!).getByText(/1 returned to \(4 completions in total\)/)).toBeInTheDocument()
    // The rating is reported as given, not amplified by the rewatching.
    expect(within(card!).getByText(/average 9\/10/)).toBeInTheDocument()
  })

  // --- empty and early states ----------------------------------------------

  it('handles a user with no activity at all', async () => {
    await renderSignedIn(
      overview({
        summary: {
          total_interactions: 0,
          works_rated: 0,
          signals_with_direction: 0,
          concepts_awaiting_ratings: 0,
          rating_context_established: false,
          interactions_without_concepts: 0,
        },
      }),
    )

    expect(await screen.findByText('Not enough activity yet')).toBeInTheDocument()
  })

  it('explains activity without ratings', async () => {
    await renderSignedIn(
      overview({
        summary: {
          total_interactions: 3,
          works_rated: 0,
          signals_with_direction: 0,
          concepts_awaiting_ratings: 0,
          rating_context_established: false,
          interactions_without_concepts: 0,
        },
      }),
    )

    expect(
      await screen.findByText('No ratings yet, so no preference directions'),
    ).toBeInTheDocument()
  })

  it('says early signals are tentative after one rating', async () => {
    await renderSignedIn(
      overview({
        summary: {
          total_interactions: 1,
          works_rated: 1,
          signals_with_direction: 1,
          concepts_awaiting_ratings: 0,
          rating_context_established: false,
          interactions_without_concepts: 0,
        },
        signals: [
          signal(
            'Psychological Depth',
            'positive',
            'low',
            counts({ works_exposed: 1, works_completed: 1, works_rated: 1, rating_mean: 10 }),
          ),
        ],
      }),
    )

    expect(await screen.findByText(/Early signals are tentative/)).toBeInTheDocument()
  })

  it('reports a content coverage gap as Noema’s, not the reader’s', async () => {
    await renderSignedIn(
      overview({
        summary: {
          total_interactions: 3,
          works_rated: 3,
          signals_with_direction: 0,
          concepts_awaiting_ratings: 0,
          rating_context_established: false,
          interactions_without_concepts: 2,
        },
      }),
    )

    expect(await screen.findByText(/gap in Noema/)).toBeInTheDocument()
  })

  it('surfaces a server error instead of rendering nothing', async () => {
    await renderSignedIn(null, 500)

    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })

  // --- the boundary this phase exists to hold ------------------------------

  it('uses no personality language anywhere on the page', async () => {
    await renderSignedIn(CASE_A)
    const user = userEvent.setup()
    await user.click(await screen.findByText('How this works'))
    await user.click(screen.getByText(/Contributing works/))

    const text = (document.body.textContent ?? '').toLowerCase()
    for (const forbidden of [
      'introvert',
      'extrovert',
      'openness',
      'conscientious',
      'neurotic',
      'empath',
      'emotional stability',
      'you are ',
      'this proves',
      'trait',
    ]) {
      expect(text).not.toContain(forbidden)
    }
    // "personality" appears only where the page denies making such a claim.
    for (const match of text.matchAll(/personality/g)) {
      const context = text.slice(Math.max(0, match.index - 20), match.index + 12)
      expect(context).toMatch(/not (your|a) personality/)
    }
  })

  it('says outright that it is not a personality assessment', async () => {
    await renderSignedIn(CASE_A)
    const user = userEvent.setup()

    expect(await screen.findByText(/not your personality/)).toBeInTheDocument()
    await user.click(screen.getByText('How this works'))
    expect(screen.getByText(/not a personality assessment/)).toBeInTheDocument()
  })

  it('makes no recommendation', async () => {
    await renderSignedIn(CASE_A)
    await screen.findByText('Psychological Depth')

    const text = (document.body.textContent ?? '').toLowerCase()
    for (const forbidden of ['because you', 'watch next', 'recommend', 'you might like', 'try ']) {
      expect(text).not.toContain(forbidden)
    }
  })

  it('renders no internal corpus or engine fields', async () => {
    await renderSignedIn(CASE_A)
    const user = userEvent.setup()
    await user.click(await screen.findByText(/Contributing works/))

    const text = document.body.textContent ?? ''
    for (const forbidden of [
      'content_unit',
      'embedding',
      'supporting_labels',
      'normalized',
      'baseline',
      'shrink',
      'concept_confidence',
      'work_id',
    ]) {
      expect(text).not.toContain(forbidden)
    }
  })

  it('explains the rating context without exposing the formula', async () => {
    await renderSignedIn(CASE_A)
    const user = userEvent.setup()

    await user.click(await screen.findByText('How this works'))

    expect(screen.getByText(/in the context of how you usually rate/)).toBeInTheDocument()
    const text = document.body.textContent ?? ''
    expect(text).not.toMatch(/prior|shrink|midpoint|variance|standard deviation/i)
  })

  // --- accessibility -------------------------------------------------------

  it('keeps evidence details reachable by keyboard', async () => {
    await renderSignedIn(CASE_A)
    const user = userEvent.setup()

    const summary = await screen.findByText(/Contributing works/)
    const details = summary.closest('details')
    expect(details).not.toBeNull()
    expect(details).not.toHaveAttribute('open')

    // The evidence lives in <details>/<summary> precisely so it needs no
    // custom keyboard handling: the summary is focusable by default and a
    // browser toggles it on Enter or Space. jsdom does not simulate that
    // native activation, so what is checked here is that the control really
    // is keyboard-reachable and that activating it reveals the works.
    summary.focus()
    expect(document.activeElement).toBe(summary)
    expect(summary.tagName.toLowerCase()).toBe('summary')

    await user.click(summary)

    await waitFor(() => expect(details).toHaveAttribute('open'))
    expect(screen.getByText('serial experiments lain')).toBeInTheDocument()
  })

  it('gives every section a heading', async () => {
    await renderSignedIn(CASE_A)

    expect(await screen.findByRole('heading', { name: 'Your preference evidence' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Preference signals' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Psychological Depth' })).toBeVisible()
  })
})
