import { apiPost } from './client'
import type { SemanticSearchResponse, WorkSearchResponse } from '../types/api'

/**
 * Meaning-oriented retrieval over the embedded corpus.
 *
 * Public: the endpoint takes no account and this sends no token.
 *
 * It used to call `fetch` directly, which meant it was the one path in the
 * app that did not get the shared error handling -- a 503 surfaced to the
 * reader as "semantic search failed with 503" and a dropped connection as
 * "Failed to fetch". Going through `apiPost` gives it the same normalized,
 * reader-facing messages as everything else. The request and response
 * contract is unchanged.
 */
export interface SemanticSearchParams {
  query: string
  top_k?: number
  domain?: string | null
  text_tier?: string | null
  representation?: string
}

export function semanticSearch(
  params: SemanticSearchParams,
): Promise<SemanticSearchResponse> {
  return apiPost<SemanticSearchResponse>('/api/v1/search/semantic', {
    query: params.query,
    top_k: params.top_k ?? 10,
    domain: params.domain ?? null,
    text_tier: params.text_tier ?? null,
    representation: params.representation ?? 'content_unit',
  })
}

/**
 * The same search, answered in works.
 *
 * The product surface. `semanticSearch` above returns the raw passages the
 * vectors matched and backs the retrieval-inspection page; this returns the
 * works those passages belong to, so a novel that matches in four places is
 * one result rather than four. `top_k` means unique works.
 */
export function workSearch(params: SemanticSearchParams): Promise<WorkSearchResponse> {
  return apiPost<WorkSearchResponse>('/api/v1/search/works', {
    query: params.query,
    top_k: params.top_k ?? 10,
    domain: params.domain ?? null,
    text_tier: params.text_tier ?? null,
    representation: params.representation ?? 'content_unit',
  })
}
