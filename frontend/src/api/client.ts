import { API_BASE_URL } from '../lib/config'

/**
 * One request mechanism, one token, one error shape.
 *
 * Before this, three things were spread around: `apiGet` here, an
 * `authenticatedRequest` living inside `api/library.ts`, and a module-level
 * token variable that only that file could see. Every other API module
 * imported the request function *from the library module*, which is the wrong
 * direction and made "where does the token come from" a question with more
 * than one answer.
 *
 * ---
 *
 * Error normalization, and the `[object Object]` bug
 *
 * The old code did this:
 *
 *     const body = (await response.json()) as { detail?: string }
 *     if (body.detail) detail = body.detail
 *
 * The cast was a lie. FastAPI answers a validation failure with `detail` as an
 * **array of objects**, not a string:
 *
 *     {"detail": [{"type": "string_too_short",
 *                  "loc": ["body", "password"],
 *                  "msg": "String should have at least 10 characters",
 *                  "ctx": {"min_length": 10}}]}
 *
 * Assigning that array to a string variable and handing it to `new Error()`
 * produced exactly `[object Object]` on screen. So `detail` is now parsed
 * rather than asserted, and nothing from a response body is rendered without
 * passing through `describe()` first.
 */

/** What a caller can act on: a status, and a sentence fit to show a person. */
export class ApiError extends Error {
  status: number
  /** Field-scoped messages, when the server sent a validation failure. */
  fields: Record<string, string>

  constructor(status: number, message: string, fields: Record<string, string> = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.fields = fields
  }
}

/** Raised when the request never reached the server at all. */
export class NetworkError extends Error {
  constructor(message = 'Could not reach Noema. Check your connection and try again.') {
    super(message)
    this.name = 'NetworkError'
  }
}

interface ValidationItem {
  loc?: unknown[]
  msg?: string
  type?: string
  ctx?: { min_length?: number; max_length?: number }
}

/** The field a Pydantic error points at: `["body", "password"]` -> password. */
function fieldOf(item: ValidationItem): string {
  const loc = Array.isArray(item.loc) ? item.loc : []
  const last = loc[loc.length - 1]
  return typeof last === 'string' ? last : ''
}

/**
 * One validation item as a sentence.
 *
 * Pydantic's own wording ("String should have at least 10 characters") is
 * accurate and unusable: it names a type rather than the thing the reader
 * typed. Known fields get a sentence; anything else falls back to the
 * server's message, which is still better than a rendered object.
 */
function describeValidation(item: ValidationItem): { field: string; message: string } {
  const field = fieldOf(item)
  const min = item.ctx?.min_length

  if (field === 'password') {
    if (item.type === 'string_too_short') {
      return {
        field,
        message: `Password must be at least ${min ?? 10} characters.`,
      }
    }
    if (item.type === 'missing') {
      return { field, message: 'Please enter a password.' }
    }
  }

  if (field === 'email') {
    if (item.type === 'missing') {
      return { field, message: 'Please enter your email address.' }
    }
    return { field, message: 'Please enter a valid email address.' }
  }

  return { field, message: item.msg ?? 'That value is not valid.' }
}

/**
 * A response body reduced to something showable.
 *
 * `detail` arrives as a string, as an array of validation items, or as an
 * object. Only the first is safe to print directly, and even then only
 * because the backend writes those strings for people.
 */
function describe(
  status: number,
  body: unknown,
): { message: string; fields: Record<string, string> } {
  const detail = (body as { detail?: unknown } | null)?.detail

  if (Array.isArray(detail)) {
    const fields: Record<string, string> = {}
    const messages: string[] = []
    for (const raw of detail) {
      const { field, message } = describeValidation(raw as ValidationItem)
      if (field && !fields[field]) fields[field] = message
      messages.push(message)
    }
    return { message: messages[0] ?? fallback(status), fields }
  }

  if (typeof detail === 'string' && detail.trim()) {
    return { message: sentence(detail), fields: {} }
  }

  // An object, a null, or nothing at all: never rendered, always replaced.
  return { message: fallback(status), fields: {} }
}

/** The backend writes lowercase detail strings; a UI shows sentences. */
function sentence(value: string): string {
  const trimmed = value.trim()
  const capitalised = trimmed.charAt(0).toUpperCase() + trimmed.slice(1)
  return /[.!?]$/.test(capitalised) ? capitalised : `${capitalised}.`
}

/** What to say when the body told us nothing useful. */
function fallback(status: number): string {
  if (status === 401) return 'Your session has expired. Please log in again.'
  if (status === 403) return 'You do not have access to that.'
  if (status === 404) return 'That could not be found.'
  if (status === 409) return 'That conflicts with something that already exists.'
  if (status === 429) return 'Too many attempts. Please try again later.'
  if (status >= 500) return 'Something went wrong. Please try again.'
  return 'That request could not be completed.'
}

/* -------------------------------------------------------------------------
 * The session token
 * ---------------------------------------------------------------------- */

const TOKEN_KEY = 'noema.session'

/**
 * Held in `localStorage` so a refresh does not sign the reader out.
 *
 * This is a real tradeoff and worth naming: a token in `localStorage` is
 * readable by any script that gets injected into the page, where an
 * `HttpOnly` cookie would not be. The backend issues opaque bearer tokens
 * that it can revoke server-side, and moving to cookie sessions is a backend
 * change this phase is explicitly not making. Revisit it when that changes.
 *
 * The in-memory copy is the source of truth during a session; storage is only
 * how it survives a reload. Reads are wrapped because storage throws in
 * private-mode and sandboxed contexts.
 */
let sessionToken: string | null = readStoredToken()

function readStoredToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function getSessionToken(): string | null {
  return sessionToken
}

export function setSessionToken(token: string | null): void {
  sessionToken = token
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token)
    else window.localStorage.removeItem(TOKEN_KEY)
  } catch {
    // Storage unavailable. The session still works for this tab; it just
    // will not survive a reload, which is the old behaviour.
  }
}

/* -------------------------------------------------------------------------
 * Requests
 * ---------------------------------------------------------------------- */

async function request<T>(path: string, init: RequestInit, auth: boolean): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body) headers.set('Content-Type', 'application/json')
  // Read at call time, never captured: a request made after login must carry
  // the token login just set.
  if (auth && sessionToken) headers.set('Authorization', `Bearer ${sessionToken}`)

  let response: Response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, { ...init, headers })
  } catch {
    throw new NetworkError()
  }

  if (!response.ok) {
    let body: unknown = null
    try {
      body = await response.json()
    } catch {
      // A non-JSON error body is fine; `describe` falls back on the status.
    }
    const { message, fields } = describe(response.status, body)
    throw new ApiError(response.status, message, fields)
  }

  return (response.status === 204 ? undefined : await response.json()) as T
}

/** A public request. Sends no token even when one exists. */
export function apiGet<T>(path: string): Promise<T> {
  return request<T>(path, {}, false)
}

/**
 * A public POST. Sends no token even when one exists.
 *
 * Semantic search is the only one: it takes a body, needs no account, and
 * must still fail in the product's words rather than in the network's.
 */
export function apiPost<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: 'POST', body: JSON.stringify(body) }, false)
}

/**
 * A request carrying the current session token.
 *
 * Every private call in the app goes through this, so there is exactly one
 * place that knows how a token is attached. Endpoints taking *optional*
 * authentication use it too: signing in should change what `/works` returns
 * for a reader, and it only can if the token travels.
 */
export function authenticatedRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  return request<T>(path, init, true)
}
