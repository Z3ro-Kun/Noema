import type { PreferenceOverview } from '../types/api'
import { authenticatedRequest } from './library'

/**
 * The preference page's data.
 *
 * Authenticated: the server derives the user from the session token and
 * accepts no user identifier, so there is nothing here to point elsewhere.
 */
export function fetchPreferenceOverview(): Promise<PreferenceOverview> {
  return authenticatedRequest<PreferenceOverview>('/api/v1/preferences/overview')
}
