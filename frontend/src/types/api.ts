export interface HealthResponse {
  status: 'ok' | 'degraded'
  database: 'connected' | 'unavailable'
  version: string
}

export type DomainSlug = 'literature' | 'anime' | 'manhwa'

export interface WorkSummary {
  id: string
  title: string
  original_title: string | null
  domain_slug: string
  source: string | null
  created_at: string
}

export interface WorkDetail extends WorkSummary {
  description: string | null
  external_ids: Record<string, unknown> | null
  extra_metadata: Record<string, unknown> | null
}

export interface Container {
  id: string
  container_type: string
  sequence_number: number
  title: string | null
  extra_metadata: Record<string, unknown> | null
  content_unit_count: number
}

export interface TextSource {
  source_name: string
  source_ref: string
  source_url: string | null
  revision_ref: string | null
  retrieved_at: string
  licence: string
  licence_url: string | null
  attribution_text: string | null
  requires_attribution: boolean
  share_alike: boolean
}

export interface ContentUnit {
  id: string
  unit_type: string
  sequence_number: number
  text_content: string | null
  /** "primary" = the work's own words; "summary" = a third party describing it. */
  text_tier: 'primary' | 'summary'
  text_source: TextSource | null
  extra_metadata: Record<string, unknown> | null
}

export interface SearchHit {
  /** Cosine similarity between embedding vectors, not a stated relationship. */
  similarity: number
  distance: number
  text_excerpt: string
  text_tier: 'primary' | 'summary'
  /** "content_unit" = a source unit; "contextual_passage" = derived from several. */
  representation: 'content_unit' | 'contextual_passage'
  work_id: string
  work_title: string
  domain_slug: string
  /**
   * Null together when the unit describes the whole work rather than sitting
   * anywhere inside it -- the fallback for works the canonical source
   * catalogues no chapters or episodes for.
   */
  container_id: string | null
  container_type: string | null
  container_title: string | null
  container_sequence_number: number | null
  content_unit_id: string | null
  unit_type: string | null
  sequence_number: number | null
  source_name: string | null
  source_url: string | null
  licence: string | null
  passage_id: string | null
  source_unit_ids: string[] | null
  unit_count: number | null
  first_unit_sequence: number | null
  last_unit_sequence: number | null
  grouping_config: string | null
}

export interface SemanticSearchResponse {
  query: string
  model_name: string
  metric: string
  result_kind: string
  top_k: number
  domain: string | null
  text_tier: string | null
  representation: string
  hits: SearchHit[]
}

/**
 * One work that matched a meaning search.
 *
 * Extends `WorkPresentation` on the wire, so a search result renders with the
 * same card as anywhere else and keeps the same canonical/personal split.
 * `similarity` is the strongest underlying passage similarity -- not an
 * average, not a blend, and not a claim that the work is *about* the query.
 */
export interface WorkSearchEvidence {
  /** Null together when the strongest passage describes the work as a whole. */
  container_id: string | null
  container_type: string | null
  container_title: string | null
  container_sequence_number: number | null
  text_tier: 'primary' | 'summary'
  /** How many candidate passages belonged to this work. Context, not a score. */
  matching_passages: number
  /** Short by contract. The corpus is not a reading interface. */
  excerpt: string
  source_name: string | null
  licence: string | null
}

export interface WorkSearchHit extends WorkPresentation {
  similarity: number
  distance: number
  representation: 'content_unit' | 'contextual_passage'
  evidence: WorkSearchEvidence
}

/** `top_k` counts unique works here, never raw passages. */
export interface WorkSearchResponse {
  query: string
  model_name: string
  metric: string
  result_kind: string
  top_k: number
  domain: string | null
  text_tier: string | null
  representation: string
  /** How many raw passages were examined to produce these works. */
  candidates_examined: number
  results: WorkSearchHit[]
}

export interface Entity {
  id: string
  name: string
  entity_type: string
  description: string | null
  extra_metadata: Record<string, unknown> | null
}

export interface Relationship {
  id: string
  predicate: string
  object_type: string
  object_id: string
  object_title: string | null
  /** "source" = a source asserted this; "computed" = our pipeline inferred it. */
  source: string
  method: string | null
  score: number | null
  confidence: number | null
}

/** A user's relationship with canonical content. Never a copy of it. */
export type LibraryStatus =
  | 'planned'
  | 'in_progress'
  | 'on_hold'
  | 'completed'
  | 'abandoned'

/** The user-facing slice of a canonical Work: no content units, no text. */
export interface LibraryWork {
  id: string
  title: string
  original_title: string | null
  domain_slug: string
  source: string | null
}

export interface InteractionRead {
  id: string
  work: LibraryWork
  status: LibraryStatus
  /** null means unrated, which is not the same as a low rating. */
  rating: number | null
  rated_at: string | null
  added_at: string
  started_at: string | null
  completed_at: string | null
  abandoned_at: string | null
  removed_at: string | null
  times_started: number
  times_completed: number
  in_library: boolean
  updated_at: string
}

export interface UserRead {
  id: string
  email: string
  display_name: string | null
  created_at: string
}

export interface SessionRead {
  access_token: string
  token_type: string
  expires_at: string
  user: UserRead
}

/** --- product surface -------------------------------------------------
 *
 * What the user-facing product shows. Deliberately free of ingestion
 * internals: no containers, content units, embeddings or provenance.
 */

export interface ProductCreator {
  name: string
  role: string
}

export interface ProductConcept {
  slug: string
  name: string
  concept_type: 'theme' | 'motif' | 'genre'
}

export interface ProductDomain {
  slug: string
  name: string
}

/** Canonical and identical for every viewer, signed in or not. */
export interface ProductWork {
  id: string
  title: string
  original_title: string | null
  domain: ProductDomain
  /** The source's own description. Null where no source supplied one. */
  synopsis: string | null
  /** Null throughout the current corpus: no source recorded cover art. */
  cover_image_url: string | null
  /** Source-native labels. Empty where the source states none. */
  genres: string[]
  /** Noema's normalized cross-domain vocabulary. */
  concepts: ProductConcept[]
  creators: ProductCreator[]
  media_format: string | null
  year: number | null
  source: string | null
}

/** One user's own relationship with a work. Never shown to anyone else. */
export interface UserWorkState {
  status: LibraryStatus
  /** Null means unrated, which is not a low rating. */
  rating: number | null
  rated_at: string | null
  added_at: string
  started_at: string | null
  completed_at: string | null
  abandoned_at: string | null
  removed_at: string | null
  times_started: number
  times_completed: number
  in_library: boolean
}

/**
 * Two objects, never flattened: nothing user-specific can be mistaken for a
 * property of the work. `user_state` is null when anonymous or unheld.
 */
export interface WorkPresentation {
  work: ProductWork
  user_state: UserWorkState | null
}

// --- library (Phase 1Z) ----------------------------------------------------

/** One page of a reader's own library. `total` is what matched the filter. */
export interface LibraryPage {
  items: WorkPresentation[]
  total: number
  page: number
  page_size: number
}

/**
 * How much is in each part of a library, so a tabbed view can label its tabs
 * without fetching every tab. Every status appears, even at zero.
 */
export interface LibrarySummary {
  /** Currently held. Soft-removed entries are counted separately. */
  total: number
  by_status: Record<string, number>
  removed: number
  rated: number
}

/**
 * What happened with a work, in a reader's words.
 *
 * A projection of the stored event log, never the log: no event ids, no
 * internal event types. `returned` is an add after a removal; `restarted` is
 * a start after a completion, which is how reconsumption becomes legible.
 */
export type HistoryKind =
  | 'added'
  | 'returned'
  | 'started'
  | 'restarted'
  | 'completed'
  | 'paused'
  | 'abandoned'
  | 'planned'
  | 'rated'
  | 'rating_cleared'
  | 'removed'

export interface HistoryEntry {
  kind: HistoryKind
  occurred_at: string
  /** Present only on `rated`: the rating given, never a derived value. */
  rating: number | null
}

export interface LibraryHistory {
  entries: HistoryEntry[]
  times_started: number
  times_completed: number
  current_status: LibraryStatus
  rating: number | null
  in_library: boolean
}

/** Everything the library listing accepts. All optional. */
export interface LibraryQuery {
  status?: LibraryStatus | null
  domain?: string | null
  include_removed?: boolean
  page?: number
  page_size?: number
}

// --- discovery (Phase 1Y) --------------------------------------------------
//
// Browsing is a page, never "the corpus". The listing endpoint has always
// been able to return everything today -- seventeen works -- and an endpoint
// whose contract is "everything" has to be redesigned the first time it is
// not, together with every client written against it.

export interface WorkListResponse {
  items: WorkPresentation[]
  /** Everything that matched, not what is on this page. */
  total: number
  page: number
  page_size: number
}

/** One value a filter can take, and how many works it matches. */
export interface FacetValue {
  /** What the client sends back: a domain slug, a concept slug, or a genre. */
  value: string
  /** What the client shows. Identical to `value` for genres, deliberately. */
  label: string
  count: number
}

/**
 * What the filters would actually match, fetched rather than hardcoded.
 *
 * Noema's metadata is unevenly covered on purpose: every work has a domain,
 * most have concepts, and only AniList-sourced works have genres. A client
 * that hardcoded a genre list would offer literature readers a filter that
 * can never return anything.
 */
export interface DiscoveryFacets {
  domains: FacetValue[]
  concepts: FacetValue[]
  genres: FacetValue[]
}

/** Everything the discovery listing accepts. All optional, all AND-ed. */
export interface DiscoveryQuery {
  domain?: string | null
  concept?: string | null
  genre?: string | null
  /** Lexical title text. Not semantic search -- that is a different endpoint. */
  q?: string | null
  page?: number
  page_size?: number
}

/** --- preference evidence ---------------------------------------------
 *
 * Media preference evidence, not personality. Every field describes what the
 * user watched, read and rated, and what those ratings show about concepts.
 *
 * The backend deliberately sends no raw scores: direction and a confidence
 * *band* carry the meaning without a 0.81 inviting "81% certain". It also
 * sends no normalization internals and no content-annotation confidence.
 */

/** "unknown" never appears in `signals` -- those concepts arrive in `awaiting_ratings`. */
export type PreferenceDirection = 'positive' | 'negative' | 'neutral' | 'unknown'

export type ConfidenceBand = 'low' | 'moderate' | 'high'

/** One work behind a signal, and what this user did with it. */
export interface ContributingWork {
  work_id: string
  title: string
  domain_name: string
  /** Null when never rated -- shown as "not rated", never as a zero. */
  rating: number | null
  status: LibraryStatus
  times_completed: number
  /** False once removed from the library; the evidence survives removal. */
  in_library: boolean
}

export interface EvidenceCounts {
  works_exposed: number
  works_started: number
  works_completed: number
  works_rated: number
  positive_ratings: number
  negative_ratings: number
  /** The plain average on the 1-10 scale, not the engine's normalized reading. */
  rating_mean: number | null
  works_reconsumed: number
  total_completions: number
  works_abandoned: number
  works_on_hold: number
}

/** A concept with a direction, supported by explicit ratings. */
export interface PreferenceSignal {
  concept_slug: string
  concept_name: string
  concept_type: 'theme' | 'motif' | 'genre'
  direction: PreferenceDirection
  confidence_band: ConfidenceBand
  evidence: EvidenceCounts
  contributions: ContributingWork[]
}

/** A concept met but never rated. Engagement, not approval. */
export interface ExposureSignal {
  concept_slug: string
  concept_name: string
  concept_type: 'theme' | 'motif' | 'genre'
  evidence: EvidenceCounts
  contributions: ContributingWork[]
}

export interface PreferenceSummary {
  total_interactions: number
  works_rated: number
  signals_with_direction: number
  concepts_awaiting_ratings: number
  /** True once the user's own rating history carries most of the weight. */
  rating_context_established: boolean
  /** Works carrying no concepts: a coverage gap, not a lack of interest. */
  interactions_without_concepts: number
}

export interface PreferenceOverview {
  summary: PreferenceSummary
  /** Ordered by the backend. The client renders as received and adds no ranking. */
  signals: PreferenceSignal[]
  awaiting_ratings: ExposureSignal[]
}

// --- Taste dashboard (Phase 1W) --------------------------------------------
//
// The product contract for the taste profile. Nothing here is a score: a
// group says how much the reader liked something, a confidence band says how
// much evidence there is for saying so, and the two are independent -- a
// strong preference with moderate confidence is a normal answer.
//
// `presentation_key` is a controlled key, never a sentence. The renderer
// chooses the wording; the backend deliberately does not.

/** Which semantic group a preference belongs to. */
export type PreferenceBucket = 'strongly_likes' | 'mildly_likes' | 'dislikes' | 'emerging'

/** Controlled key a renderer maps to wording. */
export type PresentationKey =
  | 'enjoys_feature'
  | 'negative_feature'
  | 'enjoys_combination'
  | 'negative_combination'
  | 'cross_domain_feature'
  | 'cross_domain_combination'
  | 'mixed_directions'
  | 'emerging_feature'
  | 'emerging_combination'

/** Where a profile is in its life, so a client can choose what to show. */
export type ProfileState = 'no_activity' | 'no_ratings' | 'building' | 'established'

/** One concept. A combination keeps both rather than inventing a joint name. */
export interface TasteFeature {
  key: string
  name: string
}

/** Counts a reader can check a preference against. No scoring internals. */
export interface TasteEvidenceSummary {
  rated_works: number
  supporting_works: number
  domains: string[]
  /** Reported beside the ratings; repetition never moves the preference. */
  includes_reconsumed_works: boolean
  /** The ratings behind this preference fall on both sides of the baseline. */
  has_mixed_evidence: boolean
}

export interface TastePreferenceItem {
  key: string
  display_name: string
  features: TasteFeature[]
  kind: 'individual' | 'combination'
  direction: PreferenceDirection
  confidence_band: ConfidenceBand
  presentation_key: PresentationKey
  domains: string[]
  evidence_summary: TasteEvidenceSummary
  /** Findings the evidence cannot tell apart from this one, by display name. */
  also_supported_by: string[]
}

/** An observation that says something the group listings do not. */
export interface TasteStandoutObservation {
  observation: 'pattern_highlight' | 'combination_highlight' | 'cross_domain_pattern' | 'opposing_directions'
  presentation_key: PresentationKey
  features: TasteFeature[]
  domains: string[]
  confidence_band: ConfidenceBand | null
  rated_works: number | null
}

export interface TasteDashboardSummary {
  profile_state: ProfileState
  rated_works: number
  established_preferences: number
  emerging_signals: number
}

export interface TasteDashboard {
  summary: TasteDashboardSummary
  /** All groups arrive ordered by the backend; the client adds no ranking. */
  strongly_likes: TastePreferenceItem[]
  mildly_likes: TastePreferenceItem[]
  dislikes: TastePreferenceItem[]
  emerging: TastePreferenceItem[]
  what_stands_out: TasteStandoutObservation[]
}

// --- Explicit preference feedback (Phase 1X) -------------------------------
//
// The other direction from the dashboard. A `TasteDashboard` is Noema's
// reading of what someone has rated; this is the reader's verdict on that
// reading, and the two are kept as separate objects for the same reason they
// are separate tables: a disagreement is not a rating and must never be able
// to be mistaken for one.

/**
 * `confirmed` -- the reader agrees the reading is reasonable.
 * `corrected` -- the reader does not. Deliberately **not** "dislikes": they
 * may simply not care about the concept, and rendering the stronger claim
 * would invent an opinion they never gave.
 */
export type PreferenceFeedbackValue = 'confirmed' | 'corrected'

/** Which product surface asked. Only the taste profile does, so far. */
export type PreferenceFeedbackSource = 'taste_profile'

export interface PreferenceFeedback {
  /** The canonical concept slug -- the same `features[].key` the dashboard sends. */
  concept_slug: string
  concept_name: string
  feedback: PreferenceFeedbackValue
  source: PreferenceFeedbackSource
  /** How many times this reader has answered about this concept. A count, not a weight. */
  submission_count: number
  first_recorded_at: string
  updated_at: string
}

export interface PreferenceFeedbackList {
  items: PreferenceFeedback[]
}

/** --- recommendations ---------------------------------------------------
 *
 * Content-based discovery. A recommendation is a catalogue work the reader
 * has not met, chosen because it carries concepts their own established
 * preferences are about -- the same preferences the taste profile shows.
 *
 * What the wire carries is the work, the preferences that matched it and a
 * confidence band. It carries no candidate score, no preference evidence
 * value and no coefficient: a reader is owed a reason they can check against
 * their own profile, not a number they would have to trust.
 */

/** Where a reader is, in the profile's own vocabulary plus one of its own. */
export type RecommendationState =
  | 'no_activity'
  | 'no_ratings'
  | 'building'
  /** Evidence exists; nothing new in the catalogue carries it. */
  | 'no_matches'
  | 'personalized'

/** One established preference a recommended work matched. */
export interface RecommendationReason {
  presentation_key: PresentationKey
  /** `positive` for a reason, `negative` for a caution. */
  direction: PreferenceDirection
  concepts: TasteFeature[]
  confidence_band: ConfidenceBand
  /** The reader's own rated works behind this preference. A checkable count. */
  rated_works: number
}

export interface Recommendation extends WorkPresentation {
  reasons: RecommendationReason[]
  /** Established dislikes this work also matches. Declared, not hidden. */
  cautions: RecommendationReason[]
  confidence_band: ConfidenceBand
}

export interface RecommendationSummary {
  state: RecommendationState
  established_preferences: number
  candidates_considered: number
  candidates_matched: number
}

export interface RecommendationResponse {
  summary: RecommendationSummary
  recommendations: Recommendation[]
}

/**
 * A standing instruction about one work's recommendability.
 *
 * Deliberately not a preference: "not interested" means "do not recommend
 * this to me", and it reaches nothing the taste profile is built from.
 */
export interface RecommendationFeedback {
  work_id: string
  action: 'not_interested'
  created_at: string
  suppressed_from_recommendations: boolean
}
