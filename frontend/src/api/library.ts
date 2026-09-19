import { authenticatedRequest, setSessionToken } from './client'
import type {
  LibraryHistory,
  LibraryPage,
  LibraryQuery,
  LibraryStatus,
  LibrarySummary,
  SessionRead,
  WorkPresentation,
} from '../types/api'

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

/**
 * Record one more deliberate completed cycle: a re-read or a re-watch.
 *
 * Phase 1AA. A POST, sent only when the reader presses the control. Nothing
 * else in this module can move the count -- reading a work, refreshing, and
 * changing status all leave it alone -- and the server returns the whole
 * presentation, so the caller re-renders from its count rather than adding
 * one to a local copy that could then disagree with the row.
 *
 * `client.ts` does not retry, and non-idempotent verbs are exactly why.
 */
export function recordReconsumption(workId: string): Promise<WorkPresentation> {
  return authenticatedRequest<WorkPresentation>(
    `/api/v1/library/${workId}/completions`,
    { method: 'POST' },
  )
}

/**
 * Take back one recorded completion.
 *
 * The inverse of `recordReconsumption`, and sent only when the reader
 * presses the control. The server refuses below a count of one -- a work
 * finished once has a reading, not a mistake -- so a 409 here is an ordinary
 * answer rather than a failure of this call.
 */
export function undoReconsumption(workId: string): Promise<WorkPresentation> {
  return authenticatedRequest<WorkPresentation>(
    `/api/v1/library/${workId}/completions`,
    { method: 'DELETE' },
  )
}

export function removeFromLibrary(workId: string): Promise<void> {
  return authenticatedRequest<void>(`/api/v1/library/${workId}`, { method: 'DELETE' })
}
