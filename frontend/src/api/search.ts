import { API_BASE_URL } from '../lib/config'
import { ApiError } from './client'
import type { SemanticSearchResponse } from '../types/api'

export interface SemanticSearchParams {
  query: string
  top_k?: number
  domain?: string | null
  text_tier?: string | null
  representation?: string
}

export async function semanticSearch(
  params: SemanticSearchParams,
): Promise<SemanticSearchResponse> {
  const response = await fetch(`${API_BASE_URL}/api/v1/search/semantic`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query: params.query,
      top_k: params.top_k ?? 10,
      domain: params.domain ?? null,
      text_tier: params.text_tier ?? null,
      representation: params.representation ?? 'content_unit',
    }),
  })

  if (!response.ok) {
    throw new ApiError(response.status, `semantic search failed with ${response.status}`)
  }
  return response.json() as Promise<SemanticSearchResponse>
}
