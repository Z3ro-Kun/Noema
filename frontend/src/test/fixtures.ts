/**
 * Shared test fixtures: the shapes the API really returns.
 *
 * Phase 1Y split the frontend suite by page, and these moved here so the
 * product surfaces, the corpus viewer and the navigation tests all assert
 * against one description of a work rather than three that can drift apart.
 *
 * Every object mirrors a real response. `presentation()` in particular wraps
 * a catalogue record in the `WorkPresentation` envelope exactly as the
 * backend does -- canonical `work`, and `user_state` that is null for anyone
 * who has no interaction with it.
 */

import type { ProductWork, UserWorkState, WorkPresentation } from '../types/api'

export const ACCOUNT = {
  id: 'user-1',
  email: 'reader@example.test',
  display_name: null,
  created_at: '2026-09-19T00:00:00Z',
}

export const SESSION = {
  access_token: 'test-token-abc',
  token_type: 'bearer',
  expires_at: '2026-10-02T00:00:00Z',
  user: ACCOUNT,
}

/** The internal catalogue record, for the corpus viewer. */
export const WORK = {
  id: 'work-1',
  title: "Alice's Adventures in Wonderland",
  original_title: null,
  domain_slug: 'literature',
  source: 'gutenberg',
  created_at: '2026-09-17T00:00:00Z',
  description: null,
  external_ids: { source_ref: '11' },
  extra_metadata: {
    provenance: {
      adapter: 'literature.plain_text',
      source_name: 'gutenberg',
      source_url: 'https://www.gutenberg.org/ebooks/11',
    },
  },
}

export const ANIME = {
  id: 'work-2',
  title: 'Cowboy Bebop',
  original_title: 'カウボーイビバップ',
  domain_slug: 'anime',
  source: 'anilist',
  created_at: '2026-09-17T00:00:00Z',
  description: 'Bounty hunters in space.',
  external_ids: { source_ref: '1', anilist_id: 1 },
  extra_metadata: {
    provenance: {
      adapter: 'anime.anilist',
      source_name: 'anilist',
      source_url: 'https://anilist.co/anime/1',
    },
    structure: { content_units_available: false },
    anilist: { format: 'TV', season: 'SPRING', season_year: 1998, genres: ['Action', 'Sci-Fi'] },
  },
}

export const CONTAINER = {
  id: 'container-1',
  container_type: 'chapter',
  sequence_number: 1,
  title: 'Down the Rabbit-Hole',
  extra_metadata: null,
  content_unit_count: 2,
}

export const UNITS = [
  {
    id: 'u1',
    unit_type: 'passage',
    sequence_number: 1,
    text_content: 'Alice was beginning to get very tired.',
    text_tier: 'primary',
    text_source: null,
    extra_metadata: null,
  },
  {
    id: 'u2',
    unit_type: 'passage',
    sequence_number: 2,
    text_content: 'So she considered in her own mind.',
    text_tier: 'primary',
    text_source: null,
    extra_metadata: null,
  },
]

/** A Wikipedia episode summary: text about the work, not the work's own words. */
export const SUMMARY_UNITS = [
  {
    id: 's1',
    unit_type: 'synopsis',
    sequence_number: 1,
    text_content: 'Spike and Jet head to the Tijuana asteroid colony.',
    text_tier: 'summary',
    text_source: {
      source_name: 'wikipedia',
      source_ref: 'List of Cowboy Bebop episodes',
      source_url: 'https://en.wikipedia.org/wiki/List_of_Cowboy_Bebop_episodes',
      revision_ref: '1368905328',
      retrieved_at: '2026-09-17T00:00:00Z',
      licence: 'CC-BY-SA-4.0',
      licence_url: 'https://creativecommons.org/licenses/by-sa/4.0/deed.en',
      attribution_text:
        '"List of Cowboy Bebop episodes", Wikipedia contributors, licensed CC BY-SA 4.0.',
      requires_attribution: true,
      share_alike: true,
    },
    extra_metadata: null,
  },
]

export const EPISODE = {
  id: 'episode-1',
  container_type: 'episode',
  sequence_number: 1,
  title: 'Asteroid Blues',
  extra_metadata: { has_source_text: false },
  content_unit_count: 1,
}

/** An episode with no text at all -- the uncovered case. */
export const EPISODE_NO_TEXT = {
  id: 'episode-2',
  container_type: 'episode',
  sequence_number: 2,
  title: 'Stray Dog Strut',
  extra_metadata: { has_source_text: false },
  content_unit_count: 0,
}

export const CHARACTERS = [
  {
    id: 'e1',
    name: 'Spike Spiegel',
    entity_type: 'character',
    description: null,
    extra_metadata: null,
  },
]

export const RELATIONSHIPS = [
  {
    id: 'r1',
    predicate: 'side_story',
    object_type: 'work',
    object_id: 'work-3',
    object_title: 'Cowboy Bebop: The Movie',
    source: 'source',
    method: 'anilist_relation',
    score: null,
    confidence: null,
  },
]

/**
 * The product-facing meaning search: works, not passages.
 *
 * Two raw hits from Alice fold into one work result, which is the whole
 * point of the endpoint -- `top_k` counts works.
 */
export function workSearchResponse(
  results: Record<string, unknown>[] = [
    {
      ...presentation(WORK),
      similarity: 0.4212,
      distance: 0.5788,
      representation: 'content_unit',
      evidence: {
        container_id: 'container-1',
        container_type: 'chapter',
        container_title: 'Down the Rabbit-Hole',
        container_sequence_number: 1,
        text_tier: 'primary',
        matching_passages: 3,
        excerpt: 'Alice was beginning to get very tired of sitting by her sister.',
        source_name: 'gutenberg',
        licence: null,
      },
    },
    {
      ...presentation(ANIME),
      similarity: 0.3901,
      distance: 0.6099,
      representation: 'content_unit',
      evidence: {
        container_id: 'container-9',
        container_type: 'episode',
        container_title: 'Asteroid Blues',
        container_sequence_number: 1,
        text_tier: 'summary',
        matching_passages: 1,
        excerpt: 'A bounty hunter drifts between jobs.',
        source_name: 'wikipedia',
        licence: 'CC BY-SA',
      },
    },
  ],
) {
  return {
    query: 'isolation',
    model_name: 'sentence-transformers/all-mpnet-base-v2',
    metric: 'cosine',
    result_kind: 'semantic_similarity',
    top_k: 10,
    domain: null,
    text_tier: null,
    representation: 'content_unit',
    candidates_examined: 60,
    results,
  }
}

export const SEARCH_RESPONSE = {
  query: 'isolation',
  model_name: 'sentence-transformers/all-MiniLM-L6-v2',
  metric: 'cosine',
  result_kind: 'semantic_similarity',
  top_k: 10,
  domain: null,
  text_tier: null,
  representation: 'content_unit',
  hits: [
    {
      representation: 'content_unit',
      passage_id: null,
      source_unit_ids: null,
      unit_count: null,
      first_unit_sequence: null,
      last_unit_sequence: null,
      grouping_config: null,
      content_unit_id: 'h1',
      similarity: 0.4212,
      distance: 0.5788,
      text_excerpt: 'Alice was beginning to get very tired of sitting by her sister.',
      text_tier: 'primary',
      unit_type: 'passage',
      sequence_number: 1,
      work_id: 'work-1',
      work_title: "Alice's Adventures in Wonderland",
      domain_slug: 'literature',
      container_id: 'container-1',
      container_type: 'chapter',
      container_title: 'Down the Rabbit-Hole',
      container_sequence_number: 1,
      source_name: null,
      source_url: null,
      licence: null,
    },
    {
      representation: 'content_unit',
      passage_id: null,
      source_unit_ids: null,
      unit_count: null,
      first_unit_sequence: null,
      last_unit_sequence: null,
      grouping_config: null,
      content_unit_id: 'h2',
      similarity: 0.1547,
      distance: 0.8453,
      text_excerpt: 'Spike spots Faye on TV as part of a cult called SCRATCH.',
      text_tier: 'summary',
      unit_type: 'synopsis',
      sequence_number: 1,
      work_id: 'work-2',
      work_title: 'Cowboy Bebop',
      domain_slug: 'anime',
      container_id: 'episode-1',
      container_type: 'episode',
      container_title: 'Brain Scratch',
      container_sequence_number: 23,
      source_name: 'wikipedia',
      source_url: 'https://en.wikipedia.org/wiki/List_of_Cowboy_Bebop_episodes',
      licence: 'CC-BY-SA-4.0',
    },
  ],
}

/** Wrap a catalogue record in the product `WorkPresentation` envelope. */
export function presentation(
  work: Record<string, unknown>,
  userState: UserWorkState | null = null,
): WorkPresentation {
  const anilist = (work.extra_metadata as Record<string, any>)?.anilist
  return {
    work: {
      id: work.id,
      title: work.title,
      original_title: work.original_title,
      domain: {
        slug: work.domain_slug,
        name:
          String(work.domain_slug).charAt(0).toUpperCase() +
          String(work.domain_slug).slice(1),
      },
      synopsis: work.description ?? null,
      cover_image_url: null,
      genres: anilist?.genres ?? [],
      concepts: [],
      creators: [],
      media_format: anilist?.format ?? null,
      year: anilist?.season_year ?? null,
      source: work.source,
    } as ProductWork,
    user_state: userState,
  }
}

/** A `WorkListResponse` page, as the discovery endpoint returns it. */
export function listPage(items: WorkPresentation[], total = items.length, page = 1) {
  return { items, total, page, page_size: 12 }
}

export function userState(overrides: Partial<UserWorkState> = {}): UserWorkState {
  return {
    status: 'planned',
    rating: null,
    rated_at: null,
    added_at: '2026-09-18T00:00:00Z',
    started_at: null,
    completed_at: null,
    abandoned_at: null,
    removed_at: null,
    times_started: 0,
    times_completed: 0,
    in_library: true,
    ...overrides,
  }
}

export const FACETS = {
  domains: [
    { value: 'literature', label: 'Literature', count: 4 },
    { value: 'anime', label: 'Anime', count: 9 },
    { value: 'manhwa', label: 'Manga & Manhwa', count: 4 },
  ],
  concepts: [
    { value: 'psychological-depth', label: 'Psychological Depth', count: 5 },
    { value: 'tragedy', label: 'Tragedy', count: 11 },
  ],
  genres: [
    { value: 'Drama', label: 'Drama', count: 9 },
    { value: 'Mystery', label: 'Mystery', count: 5 },
  ],
}

/** A `LibraryPage` envelope, as the library listing returns it since 1Z. */
export function libraryPage(items: WorkPresentation[], total = items.length, page = 1) {
  return { items, total, page, page_size: 24 }
}

/** A `LibrarySummary`, with every status present as the API guarantees. */
export function librarySummary(
  byStatus: Partial<Record<string, number>> = {},
  extra: { removed?: number; rated?: number } = {},
) {
  const counts: Record<string, number> = {
    planned: 0,
    in_progress: 0,
    on_hold: 0,
    completed: 0,
    abandoned: 0,
    ...byStatus,
  }
  return {
    total: Object.values(counts).reduce((sum, n) => sum + n, 0),
    by_status: counts,
    removed: extra.removed ?? 0,
    rated: extra.rated ?? 0,
  }
}

/** A `LibraryHistory`, the product projection of the stored event log. */
export function workHistory(
  entries: { kind: string; occurred_at?: string; rating?: number | null }[] = [],
  extra: Partial<{
    times_started: number
    times_completed: number
    current_status: string
    rating: number | null
    in_library: boolean
  }> = {},
) {
  return {
    entries: entries.map((entry) => ({
      kind: entry.kind,
      occurred_at: entry.occurred_at ?? '2026-09-12T00:00:00Z',
      rating: entry.rating ?? null,
    })),
    times_started: extra.times_started ?? 1,
    times_completed: extra.times_completed ?? 0,
    current_status: extra.current_status ?? 'planned',
    rating: extra.rating ?? null,
    in_library: extra.in_library ?? true,
  }
}

/**
 * A recommendation shelf.
 *
 * Extends the product presentation, like the wire does, so a recommendation
 * renders with the same card as a Discover result and carries the same
 * `user_state` separation.
 */
export function recommendationReason(
  name = 'Psychological Depth',
  overrides: Record<string, unknown> = {},
) {
  return {
    presentation_key: 'enjoys_feature',
    direction: 'positive',
    concepts: [{ key: name.toLowerCase().replace(/ /g, '-'), name }],
    confidence_band: 'moderate',
    rated_works: 4,
    ...overrides,
  }
}

export function recommendationResponse(
  overrides: Record<string, unknown> = {},
  recommendations: Record<string, unknown>[] = [
    {
      ...presentation(WORK),
      reasons: [recommendationReason()],
      cautions: [],
      confidence_band: 'moderate',
    },
    {
      ...presentation(ANIME),
      reasons: [recommendationReason('Mystery')],
      cautions: [recommendationReason('Horror', {
        presentation_key: 'negative_feature',
        direction: 'negative',
      })],
      confidence_band: 'high',
    },
  ],
) {
  return {
    summary: {
      state: 'personalized',
      established_preferences: 2,
      candidates_considered: 30,
      candidates_matched: 12,
      ...overrides,
    },
    recommendations,
  }
}
