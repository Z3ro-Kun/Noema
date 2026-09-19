import { API_BASE_URL } from '../lib/config'
import { ApiError } from './client'
import type {
  LibraryHistory,
  LibraryPage,
  LibraryQuery,
  LibraryStatus,
  LibrarySummary,
  SessionRead,
  WorkPresentation,
} from '../types/api'

/**
 * The session token for this dev interface.
 *
 * Held in memory only, deliberately: persisting a bearer token to
 * localStorage is a real decision with real risk, and this is a debugging
 * interface, not the product's eventual auth story. A refresh logs you out.
 */
let sessionToken: string | null = null

export function setSessionToken(token: string | null): void {
  sessionToken = token
}

export function getSessionToken(): string | null {
  return sessionToken
}

/**
 * A fetch carrying the current session token.
 *
 * Exported so other authenticated clients reuse one token and one error
 * shape instead of each keeping their own.
 */
export async function authenticatedRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  if (sessionToken) {
    headers.set('Authorization', `Bearer ${sessionToken}`)
  }

  const response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers })
  if (!response.ok) {
    let detail = `${init.method ?? 'GET'} ${path} failed with ${response.status}`
    try {
      const body = (await response.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      // A non-JSON error body is fine; the status line above is enough.
    }
    throw new ApiError(response.status, detail)
  }
  return (response.status === 204 ? undefined : await response.json()) as T
}

export async function register(email: string, password: string): Promise<SessionRead> {
  const session = await authenticatedRequest<SessionRead>('/api/v1/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
  setSessionToken(session.access_token)
  return session
}

export async function login(email: string, password: string): Promise<SessionRead> {
  const session = await authenticatedRequest<SessionRead>('/api/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  })
  setSessionToken(session.access_token)
  return session
}

export async function logout(): Promise<void> {
  await authenticatedRequest<void>('/api/v1/auth/logout', { method: 'POST' })
  setSessionToken(null)
}

function libraryParams(query: LibraryQuery): string {
  const params = new URLSearchParams()
  if (query.status) params.set('status', query.status)
  if (query.domain) params.set('domain', query.domain)
  if (query.include_removed) params.set('include_removed', 'true')
  if (query.page && query.page > 1) params.set('page', String(query.page))
  if (query.page_size) params.set('page_size', String(query.page_size))
  const encoded = params.toString()
  return encoded ? `?${encoded}` : ''
}

/**
 * One page of the reader's own library, most recently touched first.
 *
 * Filtering is the server's: asking for the completed ones must not mean
 * downloading everything and picking them out here. Soft-removed entries are
 * excluded unless `include_removed` is set -- a tidied shelf is not a shelf
 * item, and a removed work must never render as though it were still held.
 */
export function fetchLibrary(query: LibraryQuery = {}): Promise<LibraryPage> {
  return authenticatedRequest<LibraryPage>(`/api/v1/library${libraryParams(query)}`)
}

/** Counts for every status, so tabs can be labelled in one request. */
export function fetchLibrarySummary(): Promise<LibrarySummary> {
  return authenticatedRequest<LibrarySummary>('/api/v1/library/summary')
}

/**
 * What happened with one work, as a reader would describe it.
 *
 * The product projection, not the stored event log -- `/events` still exists
 * and is a development surface.
 */
export function fetchWorkHistory(workId: string): Promise<LibraryHistory> {
  return authenticatedRequest<LibraryHistory>(`/api/v1/library/${workId}/history`)
}

export function addToLibrary(workId: string): Promise<WorkPresentation> {
  return authenticatedRequest<WorkPresentation>('/api/v1/library', {
    method: 'POST',
    body: JSON.stringify({ work_id: workId }),
  })
}

export function updateLibraryEntry(
  workId: string,
  update: { status?: LibraryStatus; rating?: number | null; rating_set?: boolean },
): Promise<WorkPresentation> {
  return authenticatedRequest<WorkPresentation>(`/api/v1/library/${workId}`, {
    method: 'PATCH',
    body: JSON.stringify(update),
  })
}

export function removeFromLibrary(workId: string): Promise<void> {
  return authenticatedRequest<void>(`/api/v1/library/${workId}`, { method: 'DELETE' })
}
