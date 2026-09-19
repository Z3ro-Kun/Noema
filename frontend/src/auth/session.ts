import { useSyncExternalStore } from 'react'
import {
  ApiError,
  authenticatedRequest,
  getSessionToken,
  setSessionToken,
} from '../api/client'
import type { SessionRead, UserRead } from '../types/api'

/**
 * One authentication state for the whole application.
 *
 * ---
 *
 * The bug this replaces
 *
 * `useSession` used to be a hook holding `account` in `useState`. Five
 * components called it -- App, Home, Library, Preferences, TasteProfile --
 * and **each call created its own independent state**. The token itself was
 * shared (a module-level variable), so the network layer knew the reader was
 * signed in while the UI did not.
 *
 * That produced the reported failure exactly:
 *
 *     Home's instance signs in     -> Home's `account` becomes the email
 *     App's instance never re-runs -> App's `account` stays null
 *     App passes `account` down    -> Discover and WorkPage receive null
 *     WorkPage renders `!account`  -> "Sign in to track this"
 *
 * The reader *was* authenticated. A stale copy of the state said otherwise.
 *
 * ---
 *
 * Why an external store rather than a context
 *
 * The requirement is one state, observed identically everywhere. A context
 * would do it and would also mean every page that calls `useSession` must be
 * rendered inside a provider -- including in tests, which render pages
 * directly. `useSyncExternalStore` over a module-level store gives the same
 * single source of truth with no provider to forget and no second code path
 * for "no provider present". React subscribes every consumer to the same
 * snapshot, so a change in one place re-renders all of them.
 *
 * No Redux, no store library: this is one object, one `Set` of listeners and
 * one `emit`.
 */

export type SessionStatus =
  | 'restoring'
  | 'anonymous'
  | 'authenticating'
  | 'authenticated'

export interface SessionSnapshot {
  status: SessionStatus
  /** The signed-in account's email, or null. */
  account: string | null
  /** True while the session is being restored on startup. */
  loading: boolean
  /** True while a sign-in, sign-up or sign-out is in flight. */
  busy: boolean
  error: string | null
  /** Field-scoped validation messages from the last failed attempt. */
  fieldErrors: Record<string, string>
}

let state: SessionSnapshot = {
  // A token in storage means there is something to restore; without one the
  // reader is anonymous immediately and no request is made.
  status: getSessionToken() ? 'restoring' : 'anonymous',
  account: null,
  loading: Boolean(getSessionToken()),
  busy: false,
  error: null,
  fieldErrors: {},
}

const listeners = new Set<() => void>()

function emit(next: Partial<SessionSnapshot>): void {
  state = { ...state, ...next }
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function snapshot(): SessionSnapshot {
  return state
}

/** Errors are normalized in `api/client`; this only chooses login wording. */
function readError(caught: unknown, context: 'login' | 'register' | 'other'): {
  error: string
  fieldErrors: Record<string, string>
} {
  if (caught instanceof ApiError) {
    // A 401 on the login route means the credentials were wrong -- and says
    // so without revealing whether the account exists.
    if (caught.status === 401 && context === 'login') {
      return { error: 'Invalid email or password.', fieldErrors: {} }
    }
    if (caught.status === 409 && context === 'register') {
      return { error: 'An account with this email already exists.', fieldErrors: {} }
    }
    return { error: caught.message, fieldErrors: caught.fields }
  }
  if (caught instanceof Error) return { error: caught.message, fieldErrors: {} }
  return { error: 'Something went wrong. Please try again.', fieldErrors: {} }
}

/* -------------------------------------------------------------------------
 * Lifecycle
 * ---------------------------------------------------------------------- */

let restoring: Promise<void> | null = null

/**
 * Startup: turn a stored token back into a known account.
 *
 * Runs at most once per page load. A token the server no longer accepts is
 * cleared rather than retried, so an invalid token can never loop.
 */
export function restoreSession(): Promise<void> {
  if (restoring) return restoring
  if (!getSessionToken()) {
    if (state.status === 'restoring') {
      emit({ status: 'anonymous', loading: false, account: null })
    }
    return Promise.resolve()
  }

  restoring = (async () => {
    try {
      const user = await authenticatedRequest<UserRead>('/api/v1/auth/me')
      emit({ status: 'authenticated', account: user.email, loading: false, error: null })
    } catch {
      // Expired, revoked or malformed. Drop it and stay anonymous; this is
      // an ordinary state, not an error worth showing anyone.
      setSessionToken(null)
      emit({ status: 'anonymous', account: null, loading: false })
    } finally {
      restoring = null
    }
  })()

  return restoring
}

async function authenticate(
  path: '/api/v1/auth/login' | '/api/v1/auth/register',
  email: string,
  password: string,
  context: 'login' | 'register',
): Promise<boolean> {
  emit({ status: 'authenticating', busy: true, error: null, fieldErrors: {} })
  try {
    const session = await authenticatedRequest<SessionRead>(path, {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    })
    // Store the token *before* announcing the state change, so any effect
    // that fires on `account` already has a token to send.
    setSessionToken(session.access_token)
    emit({
      status: 'authenticated',
      account: session.user.email,
      busy: false,
      loading: false,
      error: null,
      fieldErrors: {},
    })
    return true
  } catch (caught) {
    const { error, fieldErrors } = readError(caught, context)
    emit({ status: 'anonymous', account: null, busy: false, error, fieldErrors })
    return false
  }
}

export function signIn(email: string, password: string): Promise<boolean> {
  return authenticate('/api/v1/auth/login', email, password, 'login')
}

export function signUp(email: string, password: string): Promise<boolean> {
  return authenticate('/api/v1/auth/register', email, password, 'register')
}

/**
 * Sign out.
 *
 * The server is asked to revoke the token, but the local token is cleared
 * whether or not that call succeeds: a reader who pressed Log out is signed
 * out of this browser regardless of what the network did.
 */
export async function signOut(): Promise<void> {
  emit({ busy: true, error: null })
  try {
    await authenticatedRequest<void>('/api/v1/auth/logout', { method: 'POST' })
  } catch {
    // Already expired, or offline. Nothing changes about what happens next.
  } finally {
    setSessionToken(null)
    emit({
      status: 'anonymous',
      account: null,
      busy: false,
      loading: false,
      error: null,
      fieldErrors: {},
    })
  }
}

/** Clear a stale message without touching the session itself. */
export function clearSessionError(): void {
  if (state.error || Object.keys(state.fieldErrors).length > 0) {
    emit({ error: null, fieldErrors: {} })
  }
}

/**
 * Test-only: put the store in an authenticated state synchronously.
 *
 * A test that renders one page in isolation needs the state the running
 * application would already have. The real login path is exercised by the
 * Login and shared-state tests; this is for the many cases whose subject is
 * "given a signed-in reader, what does this page do".
 */
export function setAuthenticatedForTests(
  email: string,
  token = 'test-token-abc',
): void {
  setSessionToken(token)
  state = {
    status: 'authenticated',
    account: email,
    loading: false,
    busy: false,
    error: null,
    fieldErrors: {},
  }
  for (const listener of listeners) listener()
}

/** Test-only: return the store to a known state between cases. */
export function resetSessionForTests(): void {
  setSessionToken(null)
  restoring = null
  state = {
    status: 'anonymous',
    account: null,
    loading: false,
    busy: false,
    error: null,
    fieldErrors: {},
  }
  for (const listener of listeners) listener()
}

/* -------------------------------------------------------------------------
 * The hook
 * ---------------------------------------------------------------------- */

export interface Session extends SessionSnapshot {
  signIn: (email: string, password: string) => Promise<boolean>
  signUp: (email: string, password: string) => Promise<boolean>
  signOut: () => Promise<void>
  clearError: () => void
}

/**
 * The session, as every component sees it.
 *
 * Every caller reads the same snapshot, so no component can conclude that the
 * reader is anonymous while another knows they are signed in.
 */
export function useSession(): Session {
  const current = useSyncExternalStore(subscribe, snapshot, snapshot)

  // The actions are module-level functions, so they are already stable across
  // renders; wrapping them in `useCallback` would add ceremony and no value.
  return {
    ...current,
    signIn,
    signUp,
    signOut,
    clearError: clearSessionError,
  }
}
