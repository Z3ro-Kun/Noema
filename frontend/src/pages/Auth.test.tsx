import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import App from '../App'
import Login from './Login'
import Register from './Register'
import { getSessionToken, setSessionToken } from '../api/client'
import { resetSessionForTests } from '../auth/session'
import { ACCOUNT, ANIME, FACETS, SESSION, WORK, listPage, presentation } from '../test/fixtures'

/**
 * Authentication, end to end through the shared session store.
 *
 * Three bugs are pinned here, and each one is a test rather than a comment.
 *
 *   **`[object Object]`.** FastAPI answers a validation failure with `detail`
 *   as an array of objects. The old client asserted it was a string and gave
 *   it to `new Error()`, which rendered the array. A reader with an
 *   eight-character password was told `[object Object]`.
 *
 *   **"Sign in first" while signed in.** `useSession` used to hold `account`
 *   in each component's own `useState`, so signing in on one page left every
 *   other component's copy stale. The work page then refused to show its
 *   controls to a reader who was, in fact, authenticated.
 *
 *   **A refresh signed you out.** The token lived in a module variable.
 */

/** The 422 FastAPI really sends for an 8-character password. */
const SHORT_PASSWORD_422 = {
  detail: [
    {
      type: 'string_too_short',
      loc: ['body', 'password'],
      msg: 'String should have at least 10 characters',
      input: '12345678',
      ctx: { min_length: 10 },
    },
  ],
}

interface Options {
  /** Status for the auth call; 200 means success. */
  authStatus?: number
  authBody?: unknown
  signedIn?: boolean
}

let calls: { url: string; init?: RequestInit }[] = []

function mockApi(options: Options = {}) {
  calls = []
  return vi.fn((input: string | URL, init?: RequestInit) => {
    const url = String(input)
    calls.push({ url, init })
    const json = (body: unknown, status = 200) =>
      Promise.resolve({
        ok: status < 400,
        status,
        json: () => Promise.resolve(body),
      } as Response)

    if (url.includes('/auth/login') || url.includes('/auth/register')) {
      const status = options.authStatus ?? 200
      return json(status < 400 ? SESSION : (options.authBody ?? {}), status)
    }
    if (url.includes('/auth/me')) {
      return options.signedIn
        ? json(ACCOUNT)
        : json({ detail: 'not authenticated' }, 401)
    }
    if (url.includes('/auth/logout')) return json(null, 204)
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
      if (url.includes('/summary')) {
        return json({
          total: 0,
          by_status: {
            planned: 0,
            in_progress: 0,
            on_hold: 0,
            completed: 0,
            abandoned: 0,
          },
          removed: 0,
          rated: 0,
        })
      }
      if (url.includes('/history')) return json({ detail: 'no entry' }, 404)
      if (init?.method === 'POST') {
        return json(
          presentation(WORK, {
            status: 'planned',
            rating: null,
            rated_at: null,
            added_at: '2026-09-19T00:00:00Z',
            started_at: null,
            completed_at: null,
            abandoned_at: null,
            removed_at: null,
            times_started: 0,
            times_completed: 0,
            in_library: true,
          }),
          201,
        )
      }
      return json({ items: [], total: 0, page: 1, page_size: 24 })
    }
    if (url.includes('/works/facets')) return json(FACETS)
    if (url.match(/\/works\/work-\d+$/)) return json(presentation(WORK))
    if (url.includes('/api/v1/works')) {
      return json(listPage([presentation(WORK), presentation(ANIME)]))
    }
    return Promise.reject(new Error(`unexpected request: ${url}`))
  })
}

function renderLogin(options: Options = {}, props: Record<string, unknown> = {}) {
  vi.stubGlobal('fetch', mockApi(options))
  return render(
    <Login
      onAuthenticated={() => {}}
      onRegister={() => {}}
      onHome={() => {}}
      {...props}
    />,
  )
}

function renderRegister(options: Options = {}, props: Record<string, unknown> = {}) {
  vi.stubGlobal('fetch', mockApi(options))
  return render(
    <Register
      onAuthenticated={() => {}}
      onLogin={() => {}}
      onHome={() => {}}
      {...props}
    />,
  )
}

beforeEach(() => {
    // The router reads `window.history`, which persists between tests in a
    // file: without this each test starts wherever the last one left off.
    window.history.replaceState({}, '', '/')
  resetSessionForTests()
  setSessionToken(null)
  vi.unstubAllGlobals()
})

/* -------------------------------------------------------------------------
 * Login
 * ---------------------------------------------------------------------- */

describe('Login', () => {
  it('renders a dedicated page, not a panel', () => {
    renderLogin()

    expect(screen.getByRole('heading', { level: 1, name: 'Log in' })).toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Log in' })).toBeInTheDocument()
  })

  it('signs in and reports it to the caller', async () => {
    const user = userEvent.setup()
    const onAuthenticated = vi.fn()
    renderLogin({}, { onAuthenticated })

    await user.type(screen.getByLabelText('Email'), 'reader@example.test')
    await user.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    await waitFor(() => expect(onAuthenticated).toHaveBeenCalled())
    expect(getSessionToken()).toBe('test-token-abc')
  })

  it('says invalid credentials without revealing whether the account exists', async () => {
    const user = userEvent.setup()
    renderLogin({ authStatus: 401, authBody: { detail: 'invalid email or password' } })

    await user.type(screen.getByLabelText('Email'), 'nobody@example.test')
    await user.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Invalid email or password.')
    // Nothing about whether that address is registered.
    expect(alert.textContent).not.toMatch(/no such|not found|unknown|exists/i)
    expect(getSessionToken()).toBeNull()
  })

  it('shows a loading state while the request is in flight', async () => {
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => {})),
    )
    render(<Login onAuthenticated={() => {}} onRegister={() => {}} onHome={() => {}} />)

    await user.type(screen.getByLabelText('Email'), 'reader@example.test')
    await user.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    expect(await screen.findByRole('button', { name: 'Logging in…' })).toBeDisabled()
  })

  it('offers the way to Register', async () => {
    const user = userEvent.setup()
    const onRegister = vi.fn()
    renderLogin({}, { onRegister })

    await user.click(screen.getByRole('button', { name: 'Create an account' }))
    expect(onRegister).toHaveBeenCalled()
  })

  it('reports a network failure in words', async () => {
    const user = userEvent.setup()
    vi.stubGlobal(
      'fetch',
      vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))),
    )
    render(<Login onAuthenticated={() => {}} onRegister={() => {}} onHome={() => {}} />)

    await user.type(screen.getByLabelText('Email'), 'reader@example.test')
    await user.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not reach Noema/)
  })
})

/* -------------------------------------------------------------------------
 * Register
 * ---------------------------------------------------------------------- */

describe('Register', () => {
  it('renders a dedicated page with confirmation', () => {
    renderRegister()

    expect(
      screen.getByRole('heading', { level: 1, name: 'Create an account' }),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('Email')).toBeInTheDocument()
    expect(screen.getByLabelText('Password')).toBeInTheDocument()
    expect(screen.getByLabelText('Confirm password')).toBeInTheDocument()
  })

  it('states the password rule before it is broken', () => {
    renderRegister()

    expect(screen.getByText('Must be at least 10 characters.')).toBeInTheDocument()
  })

  it('refuses a short password without asking the server', async () => {
    const user = userEvent.setup()
    renderRegister()

    await user.type(screen.getByLabelText('Email'), 'reader@example.test')
    await user.type(screen.getByLabelText('Password'), '12345678')
    await user.type(screen.getByLabelText('Confirm password'), '12345678')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(
      await screen.findByText('Password must be at least 10 characters.'),
    ).toBeInTheDocument()
    expect(calls.filter((call) => call.url.includes('/auth/register'))).toHaveLength(0)
  })

  it('refuses a mismatched confirmation', async () => {
    const user = userEvent.setup()
    renderRegister()

    await user.type(screen.getByLabelText('Email'), 'reader@example.test')
    await user.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await user.type(screen.getByLabelText('Confirm password'), 'something-else-entirely')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText('The passwords do not match.')).toBeInTheDocument()
  })

  it('refuses an address that is not an email', async () => {
    const user = userEvent.setup()
    renderRegister()

    await user.type(screen.getByLabelText('Email'), 'not-an-email')
    await user.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await user.type(screen.getByLabelText('Confirm password'), 'a-long-enough-password')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByText('Please enter a valid email address.')).toBeInTheDocument()
  })

  it('registers and signs the reader in', async () => {
    const user = userEvent.setup()
    const onAuthenticated = vi.fn()
    renderRegister({}, { onAuthenticated })

    await user.type(screen.getByLabelText('Email'), 'reader@example.test')
    await user.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await user.type(screen.getByLabelText('Confirm password'), 'a-long-enough-password')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    await waitFor(() => expect(onAuthenticated).toHaveBeenCalled())
    expect(getSessionToken()).toBe('test-token-abc')
  })

  it('says plainly when the address is already registered', async () => {
    const user = userEvent.setup()
    renderRegister({
      authStatus: 409,
      authBody: { detail: 'an account with this email already exists' },
    })

    await user.type(screen.getByLabelText('Email'), 'taken@example.test')
    await user.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await user.type(screen.getByLabelText('Confirm password'), 'a-long-enough-password')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'An account with this email already exists.',
    )
  })
})

/* -------------------------------------------------------------------------
 * The `[object Object]` regression
 * ---------------------------------------------------------------------- */

describe('structured validation errors', () => {
  it('renders a 422 as a sentence, never as [object Object]', async () => {
    const user = userEvent.setup()
    // The client-side rule is bypassed so the server's 422 is what is shown:
    // a long password here, with the server refusing it anyway.
    const { container } = renderRegister({
      authStatus: 422,
      authBody: SHORT_PASSWORD_422,
    })

    await user.type(screen.getByLabelText('Email'), 'reader@example.test')
    await user.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await user.type(screen.getByLabelText('Confirm password'), 'a-long-enough-password')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    await screen.findByRole('alert')
    expect(container.textContent).toContain('Password must be at least 10 characters.')
    // The bug, pinned.
    expect(container.textContent).not.toContain('[object Object]')
    expect(container.textContent).not.toContain('String should have at least')
  })

  it('attaches the message to the field it came from', async () => {
    const user = userEvent.setup()
    renderRegister({ authStatus: 422, authBody: SHORT_PASSWORD_422 })

    await user.type(screen.getByLabelText('Email'), 'reader@example.test')
    await user.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await user.type(screen.getByLabelText('Confirm password'), 'a-long-enough-password')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    await waitFor(() =>
      expect(screen.getByLabelText('Password')).toHaveAttribute('aria-invalid', 'true'),
    )
  })

  it('never renders a raw detail object from any status', async () => {
    const user = userEvent.setup()
    // A shape the frontend has no mapping for at all.
    const { container } = renderLogin({
      authStatus: 500,
      authBody: { detail: { unexpected: { nested: 'structure' } } },
    })

    await user.type(screen.getByLabelText('Email'), 'reader@example.test')
    await user.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Something went wrong. Please try again.',
    )
    expect(container.textContent).not.toContain('[object Object]')
    expect(container.textContent).not.toContain('nested')
  })
})

/* -------------------------------------------------------------------------
 * One session, observed everywhere
 * ---------------------------------------------------------------------- */

describe('shared authentication state', () => {
  /** Log in through the real Login page inside the real App. */
  async function loginThroughApp(user: ReturnType<typeof userEvent.setup>) {
    await user.type(screen.getByLabelText('Email'), 'reader@example.test')
    await user.type(screen.getByLabelText('Password'), 'a-long-enough-password')
    await user.click(screen.getByRole('button', { name: 'Log in' }))
  }

  it('carries the session from Login into Discover and the work page', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', mockApi())
    render(<App />)

    // Library is private, so an anonymous reader is sent to Login.
    await screen.findByRole('heading', { level: 1, name: 'Noema' })
    const nav = () => screen.getByRole('navigation', { name: 'Main' })
    await user.click(
      within(nav()).getByRole('button', { name: 'Library' }),
    )
    await screen.findByRole('heading', { level: 1, name: 'Log in' })

    await loginThroughApp(user)

    // The intended destination is restored, which means the whole app now
    // agrees the reader is authenticated.
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Library' }),
    ).toBeInTheDocument()

    // And a work page offers its controls rather than asking again.
    await user.click(within(nav()).getByRole('button', { name: 'Discover' }))
    await user.click(await screen.findByText("Alice's Adventures in Wonderland"))

    expect(
      await screen.findByRole('heading', { name: 'You and this work' }),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Add to library' }),
    ).toBeInTheDocument()
    // The bug: this must not appear for an authenticated reader.
    expect(screen.queryByText('Sign in to track this.')).not.toBeInTheDocument()
  })

  it('adds a work from the work page with the bearer token attached', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', mockApi())
    render(<App />)

    await screen.findByRole('heading', { level: 1, name: 'Noema' })
    const nav = () => screen.getByRole('navigation', { name: 'Main' })
    await user.click(within(nav()).getByRole('button', { name: 'Library' }))
    await screen.findByRole('heading', { level: 1, name: 'Log in' })
    await loginThroughApp(user)
    await screen.findByRole('heading', { level: 1, name: 'Library' })

    await user.click(within(nav()).getByRole('button', { name: 'Discover' }))
    await user.click(await screen.findByText("Alice's Adventures in Wonderland"))
    await user.click(await screen.findByRole('button', { name: 'Add to library' }))

    await waitFor(() => {
      const post = calls.find(
        (call) => call.url.includes('/api/v1/library') && call.init?.method === 'POST',
      )
      expect(post).toBeDefined()
      expect(new Headers(post?.init?.headers).get('Authorization')).toBe(
        'Bearer test-token-abc',
      )
    })
  })

  it('restores the session from storage on startup', async () => {
    setSessionToken('test-token-abc')
    vi.stubGlobal('fetch', mockApi({ signedIn: true }))
    render(<App />)

    // No login step: the stored token is resolved against /auth/me.
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Welcome back' }),
    ).toBeInTheDocument()
  })

  it('clears an invalid stored token instead of retrying it', async () => {
    setSessionToken('stale-token')
    vi.stubGlobal('fetch', mockApi({ signedIn: false }))
    render(<App />)

    await screen.findByRole('heading', { level: 1, name: 'Noema' })
    await waitFor(() => expect(getSessionToken()).toBeNull())
    const meCalls = calls.filter((call) => call.url.includes('/auth/me'))
    expect(meCalls).toHaveLength(1)
  })

  it('logging out clears the token and returns the reader to anonymous', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('fetch', mockApi())
    render(<App />)

    await screen.findByRole('heading', { level: 1, name: 'Noema' })
    const nav = () => screen.getByRole('navigation', { name: 'Main' })
    await user.click(within(nav()).getByRole('button', { name: 'Library' }))
    await screen.findByRole('heading', { level: 1, name: 'Log in' })
    await loginThroughApp(user)
    await screen.findByRole('heading', { level: 1, name: 'Library' })

    await user.click(screen.getByRole('button', { name: 'Log out' }))

    await waitFor(() => expect(getSessionToken()).toBeNull())
    // Signing out of a private page lands on Home, anonymous. It used to
    // land on Login: the guard could not tell "you need an account for this"
    // from "you just gave one up", so leaving the Library immediately asked
    // the reader to come back.
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Noema' }),
    ).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 1, name: 'Log in' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Log out' })).not.toBeInTheDocument()
  })
})
