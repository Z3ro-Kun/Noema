import type { TasteDashboard } from '../types/api'
import { authenticatedRequest } from './library'

/**
 * The taste profile.
 *
 * Authenticated: the server derives the reader from the session token and
 * accepts no user identifier, so there is nothing here to point elsewhere.
 *
 * Derived on every request from the current history, so the response reflects
 * whatever has been rated by the time it is called. Nothing is cached here --
 * a caller that wants to avoid refetching should hold the result itself.
 */
export function fetchTasteDashboard(): Promise<TasteDashboard> {
  return authenticatedRequest<TasteDashboard>('/api/v1/preferences/dashboard')
}
