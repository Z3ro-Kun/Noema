import type {
  PreferenceFeedback,
  PreferenceFeedbackList,
  PreferenceFeedbackValue,
} from '../types/api'
import { authenticatedRequest } from './library'

/**
 * What the reader has said about Noema's readings of their taste.
 *
 * Authenticated: the server derives the reader from the session token and
 * accepts no user identifier, so there is nothing here to point elsewhere.
 *
 * Fetched separately from the dashboard rather than embedded in it. The
 * dashboard is Noema's interpretation; this is the answer to it. Keeping them
 * as two requests keeps them as two ideas, which is the whole point of the
 * feature.
 */
export function fetchPreferenceFeedback(): Promise<PreferenceFeedbackList> {
  return authenticatedRequest<PreferenceFeedbackList>('/api/v1/preferences/feedback')
}

/**
 * Record whether one of Noema's readings feels right.
 *
 * `conceptSlug` is the canonical slug the dashboard handed out as
 * `features[].key` -- never a display name and never a position in a list.
 * Answering again updates the verdict and appends to its history.
 *
 * This writes no rating and moves no preference. The backend stores the
 * answer for a future phase to learn from; nothing in the profile changes as
 * a result of calling it, and the UI must not say otherwise.
 */
export function submitPreferenceFeedback(
  conceptSlug: string,
  feedback: PreferenceFeedbackValue,
): Promise<PreferenceFeedback> {
  return authenticatedRequest<PreferenceFeedback>('/api/v1/preferences/feedback', {
    method: 'POST',
    body: JSON.stringify({ concept_slug: conceptSlug, feedback }),
  })
}
