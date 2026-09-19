import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import WorkPage from './WorkPage'
import { setSessionToken } from '../api/library'
import { ANIME, WORK, presentation, userState, workHistory } from '../test/fixtures'
import type { UserWorkState } from '../types/api'

/**
 * The product work page.
 *
 * This is where the loop closes: it is the only surface that *writes* the
 * evidence the taste profile is built from. So the assertions are mostly
 * about what goes out on the wire, and about the boundary the page draws.
 *
 *   status and rating are separate fields, always. Completing something is
 *   not liking it, and `unrated` is sent as null rather than as a zero --
 *   the preference engine needs "finished, said nothing" to stay distinct
 *   from "finished, thought little of it".
 *
 *   canonical facts and the reader's own state are two regions, not one
 *   merged panel, and no request here ever names a user.
 *
 *   ingestion provenance stays in the corpus viewer, which is linked as the
 *   development surface it is.
 */

interface Options {
  state?: UserWorkState | null
  work?: Record<string, unknown>
  loadFails?: boolean
  hang?: boolean
  writeFails?: boolean
}

let calls: { url: string; init?: RequestInit }[] = []

function mockApi(options: Options = {}) {
  calls = []
  let state = options.state ?? null
  return vi.fn((input: string | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push({ url, init })
    const json = (body: unknown, status = 200) =>
      Promise.resolve({ ok: true, status, json: () => Promise.resolve(body) } as Response)

    if (url.includes('/api/v1/library')) {
      if (url.includes('/history')) {
        return state
          ? json(
              workHistory(
                [{ kind: 'added' }, { kind: 'started' }],
                {
                  current_status: state.status,
                  rating: state.rating,
                  times_completed: state.times_completed,
                  in_library: state.in_library,
                },
              ),
            )
          : Promise.resolve({
              ok: false,
              status: 404,
              json: () => Promise.resolve({ detail: 'no library entry' }),
            } as Response)
      }
      if (options.writeFails) {
        return Promise.resolve({
          ok: false,
          status: 409,
          json: () => Promise.resolve({ detail: 'already in your library' }),
        } as Response)
      }
      if (init?.method === 'DELETE') {
        state = state ? { ...state, in_library: false } : null
        return Promise.resolve({ ok: true, status: 204 } as Response)
      }
      if (init?.method === 'POST') {
        state = userState()
        return json(presentation(options.work ?? WORK, state), 201)
      }
      if (init?.method === 'PATCH') {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>
        state = { ...(state ?? userState()), ...body } as UserWorkState
        return json(presentation(options.work ?? WORK, state))
      }
    }
    if (url.includes('/api/v1/works/')) {
      if (options.hang) return new Promise<Response>(() => {})
      if (options.loadFails) {
        return Promise.resolve({
          ok: false,
          status: 404,
          json: () => Promise.resolve({ detail: 'work not found' }),
        } as Response)
      }
      return json(presentation(options.work ?? WORK, state))
    }
    return Promise.reject(new Error(`unexpected request: ${url}`))
  })
}

function renderPage(options: Options = {}, props: Record<string, unknown> = {}) {
  vi.stubGlobal('fetch', mockApi(options))
  return render(
    <WorkPage
      workId="work-1"
      onNavigate={() => {}}
      onBack={() => {}}
      onOpenCorpusViewer={() => {}}
      account="reader@example.test"
      {...props}
    />,
  )
}

/** Every library write, newest last. */
function writes() {
  return calls.filter(
    (call) => call.url.includes('/api/v1/library') && call.init?.method !== undefined,
  )
}

describe('WorkPage', () => {
  beforeEach(() => {
    setSessionToken('test-token-abc')
    vi.unstubAllGlobals()
  })

  // --- canonical facts -----------------------------------------------------

  it('shows the work from the public contract', async () => {
    renderPage({ work: ANIME })

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Cowboy Bebop' }),
    ).toBeInTheDocument()
    expect(screen.getByText('カウボーイビバップ')).toBeInTheDocument()
    expect(screen.getByText(/Anime · TV · 1998/)).toBeInTheDocument()
    expect(screen.getByText('Bounty hunters in space.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Genres' })).toBeInTheDocument()
    expect(screen.getByText('Action')).toBeInTheDocument()
  })

  it('states an absent synopsis and absent credits rather than hiding them', async () => {
    renderPage()

    expect(await screen.findByText(/No synopsis available/)).toBeInTheDocument()
    expect(screen.getByText('No credits recorded')).toBeInTheDocument()
  })

  it('keeps genres and themes as different things', async () => {
    renderPage({ work: ANIME })

    await screen.findByRole('heading', { level: 1, name: 'Cowboy Bebop' })
    expect(screen.getByText(/As stated by anilist/)).toBeInTheDocument()
    expect(screen.getByText(/Noema’s own vocabulary, shared across every medium/)).toBeInTheDocument()
  })

  it('exposes no ingestion internals, and links the viewer that does', async () => {
    const user = userEvent.setup()
    const openViewer = vi.fn()
    const { container } = renderPage({}, { onOpenCorpusViewer: openViewer })

    await screen.findByRole('heading', { level: 1, name: /Alice/ })
    const rendered = container.textContent ?? ''
    for (const forbidden of ['literature.plain_text', 'content_unit', 'embedding', 'external_ids']) {
      expect(rendered).not.toContain(forbidden)
    }

    await user.click(screen.getByRole('button', { name: 'Open in the corpus viewer' }))
    expect(openViewer).toHaveBeenCalledWith('work-1')
  })

  // --- states --------------------------------------------------------------

  it('shows a loading state', () => {
    renderPage({ hang: true })

    expect(screen.getByText('Loading this work…')).toBeInTheDocument()
  })

  it('reports a failure and offers a retry', async () => {
    const user = userEvent.setup()
    renderPage({ loadFails: true })

    expect(await screen.findByRole('alert')).toHaveTextContent('work not found')
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(calls.filter((call) => call.url.includes('/api/v1/works/')).length).toBeGreaterThan(1)
  })

  it('asks an anonymous reader to sign in without hiding the work', async () => {
    renderPage({}, { account: null })

    expect(await screen.findByRole('heading', { level: 1, name: /Alice/ })).toBeInTheDocument()
    expect(screen.getByText('Sign in to track this.')).toBeInTheDocument()
    expect(screen.queryByLabelText(/Status for/)).not.toBeInTheDocument()
  })

  // --- the interaction loop ------------------------------------------------

  it('adds a work by reference, sending only its id', async () => {
    const user = userEvent.setup()
    renderPage({ state: null })

    await user.click(await screen.findByRole('button', { name: 'Add to library' }))

    await waitFor(() => {
      const post = writes().find((call) => call.init?.method === 'POST')
      expect(JSON.parse(String(post?.init?.body))).toEqual({ work_id: 'work-1' })
    })
  })

  it('offers status and rating once the work is held', async () => {
    renderPage({ state: userState() })

    expect(await screen.findByLabelText(/Status for/)).toBeInTheDocument()
    expect(screen.getByRole('radiogroup', { name: /Your rating for/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add to library' })).not.toBeInTheDocument()
  })

  it('offers the obvious next action for each state', async () => {
    for (const [status, label] of [
      ['planned', 'Start reading'],
      ['in_progress', 'Mark completed'],
      ['on_hold', 'Pick it back up'],
      ['abandoned', 'Give it another go'],
    ] as const) {
      const { unmount } = renderPage({ state: userState({ status }) })
      expect(await screen.findByRole('button', { name: label })).toBeInTheDocument()
      unmount()
    }
  })

  it('sends status and rating as separate fields', async () => {
    const user = userEvent.setup()
    renderPage({ state: userState() })

    await user.selectOptions(await screen.findByLabelText(/Status for/), 'completed')
    await waitFor(() => {
      const patch = writes().filter((call) => call.init?.method === 'PATCH').pop()
      expect(JSON.parse(String(patch?.init?.body))).toEqual({ status: 'completed' })
    })

    await user.click(screen.getByRole('radio', { name: '9' }))
    await waitFor(() => {
      const patch = writes().filter((call) => call.init?.method === 'PATCH').pop()
      expect(JSON.parse(String(patch?.init?.body))).toEqual({ rating: 9, rating_set: true })
    })
  })

  it('clears a rating explicitly rather than sending zero', async () => {
    const user = userEvent.setup()
    renderPage({ state: userState({ rating: 7 }) })

    // "Not rated" is its own option, never a zero.
    await user.click(await screen.findByRole('radio', { name: 'Not rated' }))

    await waitFor(() => {
      const patch = writes().filter((call) => call.init?.method === 'PATCH').pop()
      expect(JSON.parse(String(patch?.init?.body))).toEqual({ rating: null, rating_set: true })
    })
  })

  it('renders an unrated work as unrated, not as zero', async () => {
    renderPage({ state: userState({ status: 'completed' }) })

    expect(await screen.findByRole('radio', { name: 'Not rated' })).toBeChecked()
    expect(screen.getByRole('radio', { name: '1' })).not.toBeChecked()
    expect((screen.getByLabelText(/Status for/) as HTMLSelectElement).value).toBe('completed')
  })

  it('says that tracking is not the same as enjoying', async () => {
    renderPage({ state: userState({ status: 'completed' }) })

    expect(
      await screen.findByText(/Rating is what tells Noema whether you enjoyed something/),
    ).toBeInTheDocument()
  })

  it('shows the current rating as chosen', async () => {
    renderPage({ state: userState({ rating: 8 }) })

    expect(await screen.findByRole('radio', { name: '8' })).toBeChecked()
    expect(screen.getByText('You rated this 8 out of 10.')).toBeInTheDocument()
  })

  it('removes a work from the library', async () => {
    const user = userEvent.setup()
    renderPage({ state: userState() })

    await user.click(
      await screen.findByRole('button', { name: /Remove .* from your library/ }),
    )

    await waitFor(() =>
      expect(writes().some((call) => call.init?.method === 'DELETE')).toBe(true),
    )
  })

  it('says that removing keeps the ratings behind it', async () => {
    renderPage({ state: userState({ in_library: false, rating: 9 }) })

    expect(
      await screen.findByText(/rating of 9\/10 and your history were kept/),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add to library' })).toBeInTheDocument()
  })

  // --- reconsumption -------------------------------------------------------

  it('offers a completed work as something to read again', async () => {
    renderPage({ state: userState({ status: 'completed', rating: 9 }) })

    expect(await screen.findByRole('button', { name: 'Read it again' })).toBeInTheDocument()
    // Reading it again is a status move, not a separate concept.
    expect(screen.queryByText(/reconsum/i)).not.toBeInTheDocument()
  })

  it('reads a work again by moving it back to in progress', async () => {
    const user = userEvent.setup()
    renderPage({ state: userState({ status: 'completed', rating: 9 }) })

    await user.click(await screen.findByRole('button', { name: 'Read it again' }))

    await waitFor(() => {
      const patch = writes().filter((call) => call.init?.method === 'PATCH').pop()
      expect(JSON.parse(String(patch?.init?.body))).toEqual({ status: 'in_progress' })
    })
    // No rating was invented, and no second entry created.
    expect(
      writes().every((call) => !String(call.init?.body ?? '').includes('rating')),
    ).toBe(true)
    expect(writes().filter((call) => call.init?.method === 'POST')).toHaveLength(0)
  })

  it('reports how many times a work has been finished', async () => {
    renderPage({ state: userState({ status: 'completed', times_completed: 3 }) })

    expect(await screen.findByText('You have finished this 3 times.')).toBeInTheDocument()
  })

  // --- history -------------------------------------------------------------

  it('shows a compact history without event internals', async () => {
    const user = userEvent.setup()
    const { container } = renderPage({ state: userState({ status: 'in_progress' }) })

    await user.click(await screen.findByText('Your history with this'))

    expect(screen.getByText('Added to your library')).toBeInTheDocument()
    expect(screen.getByText('Started')).toBeInTheDocument()
    const rendered = container.textContent ?? ''
    for (const forbidden of ['status_changed', 'rating_changed', 'event_type']) {
      expect(rendered).not.toContain(forbidden)
    }
  })

  it('shows no history for a work that is not held', async () => {
    renderPage({ state: null })

    await screen.findByRole('button', { name: 'Add to library' })
    expect(screen.queryByText('Your history with this')).not.toBeInTheDocument()
  })

  // --- the learning loop ---------------------------------------------------

  it('connects a rating to the taste profile without overclaiming', async () => {
    const user = userEvent.setup()
    const navigate = vi.fn()
    renderPage({ state: userState({ rating: 9 }) }, { onNavigate: navigate })

    expect(
      await screen.findByText(/Your rating helps Noema understand your taste/),
    ).toBeInTheDocument()
    // No claim that anything has already been recalculated.
    expect(screen.queryByText(/profile has been updated|personality/i)).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'See your taste profile' }))
    expect(navigate).toHaveBeenCalledWith('taste')
  })

  it('shows no preference numbers anywhere', async () => {
    const { container } = renderPage({ state: userState({ rating: 9 }) })

    await screen.findByRole('radiogroup', { name: /Your rating for/ })
    const rendered = container.textContent ?? ''
    for (const forbidden of ['preference_evidence', 'confidence', 'evidence']) {
      expect(rendered).not.toContain(forbidden)
    }
    // No decimal anywhere: a 0.81 on this page would read as a score.
    expect(rendered).not.toMatch(/\d\.\d/)
  })

  it('surfaces a refused write instead of failing silently', async () => {
    const user = userEvent.setup()
    renderPage({ state: null, writeFails: true })

    await user.click(await screen.findByRole('button', { name: 'Add to library' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('already in your library')
  })

  it('re-reads the work after a write rather than guessing the new state', async () => {
    const user = userEvent.setup()
    renderPage({ state: null })

    await user.click(await screen.findByRole('button', { name: 'Add to library' }))

    await waitFor(() => {
      const reads = calls.filter(
        (call) => call.url.includes('/api/v1/works/') && call.init?.method === undefined,
      )
      expect(reads.length).toBeGreaterThan(1)
    })
  })

  it('never names a user in a request', async () => {
    const user = userEvent.setup()
    renderPage({ state: userState() })

    await user.selectOptions(await screen.findByLabelText(/Status for/), 'completed')

    await waitFor(() => expect(writes().length).toBeGreaterThan(0))
    for (const call of calls) {
      expect(call.url).not.toContain('user_id')
      expect(String(call.init?.body ?? '')).not.toContain('user_id')
    }
  })

  // --- accessibility -------------------------------------------------------

  it('separates the work from the reader with labelled regions', async () => {
    renderPage({ state: userState() })

    await screen.findByRole('heading', { level: 1, name: /Alice/ })
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(screen.getByRole('heading', { name: 'You and this work' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Synopsis' })).toBeInTheDocument()
    for (const button of screen.getAllByRole('button')) {
      expect(button).toHaveAccessibleName()
    }
    // The rating is one labelled group of radios, so arrow keys work and the
    // whole scale is one tab stop rather than ten.
    const group = screen.getByRole('radiogroup', { name: /Your rating for/ })
    expect(group).toBeInTheDocument()
    expect(screen.getAllByRole('radio')).toHaveLength(11)
  })
})
