import { authenticatedRequest } from './client'
import type { RecommendationFeedback, RecommendationResponse } from '../types/api'

/**
 * Content-based discovery for the signed-in reader.
 *
 * Authenticated, and there is no user identifier to pass: the server derives
 * the reader from the session token and accepts no parameter naming one, so
 * there is nothing here that could point at somebody else's shelf.
 *
 * Derived on every request from the current history, like the taste profile
 * it is built on. Nothing is cached here.
 */
export function fetchRecommendations(limit?: number): Promise<RecommendationResponse> {
  const query = limit === undefined ? '' : `?limit=${encodeURIComponent(String(limit))}`
  return authenticatedRequest<RecommendationResponse>(`/api/v1/recommendations${query}`)
}

/**
 * "Do not recommend this work to me."
 *
 * The fifth distinct thing a reader can say, and not one of the other four:
 * it is not a rating, not a dislike of the work or its concepts, not a
 * library removal and not abandonment. It suppresses one work on one shelf
 * and touches no preference state.
 *
 * Authenticated, and there is no user identifier to pass. Idempotent -- the
 * server returns the standing instruction whether this call created it or an
 * earlier one did.
 */
export function dismissRecommendation(workId: string): Promise<RecommendationFeedback> {
  return authenticatedRequest<RecommendationFeedback>(
    `/api/v1/recommendations/${encodeURIComponent(workId)}/feedback`,
    { method: 'POST', body: JSON.stringify({ action: 'not_interested' }) },
  )
}

/** Take the instruction back, so the work can be recommended again. */
export function restoreRecommendation(workId: string): Promise<void> {
  return authenticatedRequest<void>(
    `/api/v1/recommendations/${encodeURIComponent(workId)}/feedback`,
    { method: 'DELETE' },
  )
}
