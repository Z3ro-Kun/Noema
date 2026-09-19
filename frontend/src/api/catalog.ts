import { apiGet } from './client'
import { authenticatedRequest } from './library'
import type {
  Container,
  ContentUnit,
  DiscoveryFacets,
  DiscoveryQuery,
  Entity,
  Relationship,
  WorkDetail,
  WorkListResponse,
  WorkPresentation,
} from '../types/api'

/**
 * The canonical corpus, as the product reads it.
 *
 * Two groups of calls, and the split is the same one the backend draws.
 * `fetchWorks`, `fetchWork` and `fetchDiscoveryFacets` are the **product**
 * surface: canonical works with no content units, passages, embeddings or
 * ingestion provenance. The rest -- `fetchWorkInternal`, `fetchContainers`,
 * `fetchContentUnits` -- back the development corpus viewer and are not part
 * of what a reader sees.
 *
 * The product calls go through `authenticatedRequest` rather than `apiGet`
 * because the endpoints take optional authentication: a signed-in reader gets
 * their own `user_state` attached to the same works, and an anonymous one
 * gets the identical canonical half with `user_state: null`. Sending the
 * token when there is one is the whole difference.
 */

function discoveryParams(query: DiscoveryQuery): string {
  const params = new URLSearchParams()
  // Empty strings are not filters. Sending `q=` would ask the server to
  // match nothing rather than everything, which is not what an empty search
  // box means.
  if (query.domain) params.set('domain', query.domain)
  if (query.concept) params.set('concept', query.concept)
  if (query.genre) params.set('genre', query.genre)
  if (query.q && query.q.trim()) params.set('q', query.q.trim())
  if (query.page && query.page > 1) params.set('page', String(query.page))
  if (query.page_size) params.set('page_size', String(query.page_size))
  const encoded = params.toString()
  return encoded ? `?${encoded}` : ''
}

/**
 * One page of canonical works, filtered server-side.
 *
 * Filters combine with AND. `q` is a lexical title lookup -- someone typing
 * "Monster" is looking for *Monster* -- and is deliberately not semantic
 * search, which lives in `api/search.ts` and answers a different question.
 *
 * Never fetches the whole corpus to filter it here: the server owns which
 * works match, so this keeps working when the corpus is not seventeen works.
 */
export function fetchWorks(query: DiscoveryQuery = {}): Promise<WorkListResponse> {
  return authenticatedRequest<WorkListResponse>(`/api/v1/works${discoveryParams(query)}`)
}

/** One work, with the caller's own state when they are signed in. */
export function fetchWork(workId: string): Promise<WorkPresentation> {
  return authenticatedRequest<WorkPresentation>(`/api/v1/works/${workId}`)
}

/**
 * What the discovery filters would actually match.
 *
 * Fetched rather than hardcoded so the client can hide a filter with nothing
 * behind it instead of offering an empty dropdown and calling it coverage.
 */
export function fetchDiscoveryFacets(): Promise<DiscoveryFacets> {
  return apiGet<DiscoveryFacets>('/api/v1/works/facets')
}

/** The internal catalogue record, for the development corpus viewer only. */
export function fetchWorkInternal(workId: string): Promise<WorkDetail> {
  return apiGet<WorkDetail>(`/api/v1/works/${workId}/internal`)
}

export function fetchContainers(workId: string): Promise<Container[]> {
  return apiGet<Container[]>(`/api/v1/works/${workId}/containers`)
}

export function fetchContentUnits(containerId: string): Promise<ContentUnit[]> {
  return apiGet<ContentUnit[]>(`/api/v1/containers/${containerId}/content-units`)
}

export function fetchEntities(workId: string): Promise<Entity[]> {
  return apiGet<Entity[]>(`/api/v1/works/${workId}/entities`)
}

export function fetchRelationships(workId: string): Promise<Relationship[]> {
  return apiGet<Relationship[]>(`/api/v1/works/${workId}/relationships`)
}
