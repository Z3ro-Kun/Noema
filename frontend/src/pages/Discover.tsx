import { useCallback, useEffect, useState } from 'react'
import AppShell from '../components/AppShell'
import type { ProductView } from '../components/AppShell'
import SectionHeading from '../components/SectionHeading'
import StateMessage from '../components/StateMessage'
import WorkEntry from '../components/WorkEntry'
import { fetchDiscoveryFacets, fetchWorks } from '../api/catalog'
import { semanticSearch } from '../api/search'
import { activityLine } from '../lib/labels'
import type {
  DiscoveryFacets,
  SemanticSearchResponse,
  WorkListResponse,
} from '../types/api'

/**
 * Discover -- browsing and searching the canonical corpus.
 *
 * Phase 1Y built the behaviour; this pass brings the surface into Home's
 * editorial language. Nothing about what Discover *does* changed: the same
 * three endpoints, the same server-side filtering and paging, the same two
 * modes, and the same refusals.
 *
 * ---
 *
 * Two search modes, and they are not interchangeable
 *
 *     Titles      lexical. Someone typing "Monster" is looking for *Monster*,
 *                 and that must keep working without an embedding model.
 *                 Server-side, case-insensitive, ranked exact-then-prefix.
 *
 *     Meaning     the existing semantic search, unchanged. Vector similarity
 *                 over embedded passages: it finds works whose *text* is
 *                 close to a description, which is a different question and
 *                 sometimes a surprising answer.
 *
 * Both are search. **Neither is a recommendation.** Two readers sending the
 * same query get the same results, and the page says so rather than letting
 * "for you" be inferred from a personalised-looking list. The recommendation
 * engine is a later phase and will have to earn its own labelling.
 *
 * The mode choice is the page's first editorial act rather than a segmented
 * control in a box: two halves split by a hairline, each naming what it
 * actually asks. A reader chooses how to explore before they type.
 *
 * ---
 *
 * Filtering happens on the server
 *
 * The corpus is seventeen works and would fit in one response. Filtering it
 * in React would work today and stop working at the first real import, so
 * every filter is a query parameter and the client receives a page. The
 * filter options come from `/works/facets` rather than a hardcoded list,
 * because Noema's metadata is honestly uneven: every work has a domain, most
 * have concepts, and only AniList-sourced works have genres. A hardcoded
 * genre list would offer literature readers a filter that can never match.
 *
 * ---
 *
 * Browsing is a grid of `WorkEntry`, the same unit Home's shelves use
 *
 * Which means Discover inherits the 2:3 plate, real cover art where a source
 * supplied it, the typographic fallback where none exists, and the rule that
 * fences a reader's own state off from the work's canonical facts.
 *
 * It also means the list no longer carries a synopsis or a row of genre and
 * theme chips. That is deliberate: a column of three-line clamps under a
 * wall of pills is what made this page read as a database query interface,
 * and a work's description belongs on the work's own page. `WorkCard` still
 * exists and is untouched -- Library and WorkPage use it.
 *
 * Semantic results stay text-first. A hit is a *passage*, not a work, and
 * `SearchHit` carries no cover; dressing passages up as posters would both
 * misrepresent the result and cost a fetch per hit.
 */

const PAGE_SIZE = 12
const SEMANTIC_TOP_K = 10

type Mode = 'titles' | 'meaning'

const MODES: {
  value: Mode
  label: string
  eyebrow: string
  description: string
  inputLabel: string
  placeholder: string
}[] = [
  {
    value: 'titles',
    label: 'Titles',
    eyebrow: 'By name',
    description:
      'Looks for the words you type in titles. Nothing is personalised — everyone searching the same thing sees the same works.',
    inputLabel: 'Search titles',
    placeholder: 'Search titles…',
  },
  {
    value: 'meaning',
    label: 'By meaning',
    eyebrow: 'By passage',
    description:
      'Looks for passages that mean something similar, using Noema’s embeddings. Results are text similarity, not a recommendation.',
    inputLabel: 'Describe a theme',
    placeholder: 'Describe a theme or situation…',
  },
]

/**
 * Everything a reader has chosen here, so leaving and coming back is not a
 * reset.
 *
 * Opening the retrieval surface used to drop a reader back into an empty
 * catalogue, losing the search they were in the middle of reading. The state
 * travels with them instead -- as a plain value handed to `App`, which hands
 * it back. No store, and nothing here is persisted or shared.
 */
export interface DiscoverState {
  mode: Mode
  /** What was actually searched for, never the half-typed draft. */
  query: string
  domain: string
  concept: string
  genre: string
  page: number
}

interface DiscoverProps {
  /** A filter chosen elsewhere -- a domain chip on Home, a taste shelf. */
  initialDomain?: string
  initialConcept?: string
  /** A whole state to resume. Takes precedence over the two above. */
  initialState?: DiscoverState
  onNavigate: (view: ProductView) => void
  onOpenWork: (workId: string) => void
  /** The retrieval-inspection surface, for the curious and for debugging. */
  onOpenRetrievalDetail: (state: DiscoverState) => void
}

interface Filters {
  domain: string
  concept: string
  genre: string
}

const NO_FILTERS: Filters = { domain: '', concept: '', genre: '' }

/** Small counts read better as words in a sentence; larger ones as figures. */
const NUMBER_WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six']

function inWords(count: number): string {
  return NUMBER_WORDS[count] ?? String(count)
}

/**
 * "17 works across three media", counted from the facets.
 *
 * Every number here is one the server sent. When the facets request failed
 * there is nothing to count, and the line is omitted rather than guessed at.
 */
function corpusLine(facets: DiscoveryFacets | null): string | null {
  if (!facets || facets.domains.length === 0) return null
  const works = facets.domains.reduce((sum, domain) => sum + domain.count, 0)
  if (works === 0) return null
  const media = facets.domains.length
  return `${works} ${works === 1 ? 'work' : 'works'} across ${inWords(media)} ${
    media === 1 ? 'medium' : 'media'
  }`
}

/** A labelled `<select>` built from a facet, hidden when nothing is behind it. */
function FacetSelect({
  id,
  label,
  allLabel,
  options,
  value,
  onChange,
}: {
  id: string
  label: string
  allLabel: string
  options: { value: string; label: string; count: number }[]
  value: string
  onChange: (next: string) => void
}) {
  // A filter with no values is not rendered. An empty dropdown claims a kind
  // of coverage the corpus does not have.
  if (options.length === 0) return null

  return (
    <div className="min-w-0">
      <label
        htmlFor={id}
        className="block text-[0.62rem] uppercase tracking-label text-paper-faint"
      >
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-2 w-full min-w-[10rem] appearance-none border-b border-paper/20 bg-transparent py-2 pr-6 text-[0.9rem] text-paper transition-colors duration-200 hover:border-paper/40 focus:border-accent focus-visible:outline-none sm:w-48"
      >
        {/*
          The options themselves are painted by the platform, so they carry
          their own colours rather than inheriting a transparent background.
        */}
        <option value="" className="bg-ink text-paper">
          {allLabel}
        </option>
        {options.map((option) => (
          <option key={option.value} value={option.value} className="bg-ink text-paper">
            {option.label} ({option.count})
          </option>
        ))}
      </select>
    </div>
  )
}

export default function Discover({
  initialDomain = '',
  initialConcept = '',
  initialState,
  onNavigate,
  onOpenWork,
  onOpenRetrievalDetail,
}: DiscoverProps) {
  const [mode, setMode] = useState<Mode>(initialState?.mode ?? 'titles')
  // What is typed, and what was actually searched for. Separate, so the
  // results heading cannot describe a query that was never sent. On a resume
  // the draft is seeded from the query, because the box should show what the
  // results below it are answering.
  const [draft, setDraft] = useState(initialState?.query ?? '')
  const [query, setQuery] = useState(initialState?.query ?? '')
  const [filters, setFilters] = useState<Filters>(
    initialState
      ? {
          domain: initialState.domain,
          concept: initialState.concept,
          genre: initialState.genre,
        }
      : { ...NO_FILTERS, domain: initialDomain, concept: initialConcept },
  )
  const [page, setPage] = useState(initialState?.page ?? 1)

  const [facets, setFacets] = useState<DiscoveryFacets | null>(null)
  const [results, setResults] = useState<WorkListResponse | null>(null)
  const [semantic, setSemantic] = useState<SemanticSearchResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // A facets failure must not take the page down: browsing still works
    // without the filter controls, so it degrades rather than breaks.
    void fetchDiscoveryFacets()
      .then(setFacets)
      .catch(() => setFacets(null))
  }, [])

  const browse = useCallback(async () => {
    setLoading(true)
    try {
      setResults(
        await fetchWorks({
          domain: filters.domain || null,
          concept: filters.concept || null,
          genre: filters.genre || null,
          q: mode === 'titles' ? query : null,
          page,
          page_size: PAGE_SIZE,
        }),
      )
      setError(null)
    } catch (caught) {
      setResults(null)
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoading(false)
    }
  }, [filters, query, page, mode])

  useEffect(() => {
    if (mode === 'titles') void browse()
  }, [mode, browse])

  const runSemantic = useCallback(async () => {
    if (!query.trim()) {
      setSemantic(null)
      return
    }
    setLoading(true)
    try {
      setSemantic(
        await semanticSearch({
          query,
          top_k: SEMANTIC_TOP_K,
          domain: filters.domain || null,
        }),
      )
      setError(null)
    } catch (caught) {
      setSemantic(null)
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoading(false)
    }
  }, [query, filters.domain])

  useEffect(() => {
    if (mode === 'meaning') void runSemantic()
  }, [mode, runSemantic])

  function submit(event: React.FormEvent) {
    event.preventDefault()
    setPage(1)
    setQuery(draft)
  }

  function changeFilter(key: keyof Filters, value: string) {
    setPage(1)
    setFilters((current) => ({ ...current, [key]: value }))
  }

  function switchMode(next: Mode) {
    setMode(next)
    setPage(1)
    setError(null)
  }

  function clearEverything() {
    setFilters(NO_FILTERS)
    setDraft('')
    setQuery('')
    setPage(1)
  }

  const active = MODES.find((entry) => entry.value === mode) ?? MODES[0]
  const filtered = Boolean(filters.domain || filters.concept || filters.genre || query)
  const total = results?.total ?? 0
  const first = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const last = Math.min(page * PAGE_SIZE, total)

  /** The heading over the grid. Counts only; never a claim about fit. */
  const resultTitle = `${
    total === 0 ? 'No works' : `${total} ${total === 1 ? 'work' : 'works'}`
  }${query ? ` matching “${query}”` : ''}`

  /** What is currently narrowing the list, named rather than left implicit. */
  const activeFilterNames = facets
    ? [
        facets.domains.find((option) => option.value === filters.domain)?.label,
        facets.concepts.find((option) => option.value === filters.concept)?.label,
        facets.genres.find((option) => option.value === filters.genre)?.label,
      ].filter(Boolean)
    : []

  return (
    <AppShell
      title="Discover"
      current="discover"
      onNavigate={onNavigate}
      bleed
      masthead={
        <div className="border-b border-paper/10">
          <div className="mx-auto max-w-page px-5 py-14 sm:px-6 md:py-20 lg:px-10">
            <p className="text-[0.66rem] uppercase tracking-label text-paper-faint">
              The corpus
            </p>
            <h1 className="mt-5 font-display text-[2.6rem] font-light leading-[1.05] tracking-tight text-paper sm:text-5xl lg:text-[3.6rem]">
              Discover
            </h1>
            <p className="mt-6 max-w-xl font-display text-lg font-light leading-relaxed text-paper-dim md:text-xl">
              Everything Noema holds, across literature, anime and manga — searched
              by title, or by what a passage means.
            </p>
            {corpusLine(facets) && (
              <p className="mt-8 text-[0.66rem] uppercase tracking-label text-paper-faint">
                {corpusLine(facets)}
              </p>
            )}
          </div>
        </div>
      }
    >
      {/* --- choose how to explore, then narrow it -------------------- */}

      <section aria-labelledby="explore-heading" className="border-b border-paper/10">
        <h2 id="explore-heading" className="sr-only">
          Search and filter
        </h2>

        <form onSubmit={submit}>
          <div
            role="group"
            aria-label="Search mode"
            className="border-b border-paper/10"
          >
            <div className="mx-auto grid max-w-page px-5 sm:px-6 md:grid-cols-2 md:divide-x md:divide-paper/10 lg:px-10">
              {MODES.map((entry) => {
                const current = entry.value === mode
                return (
                  <button
                    key={entry.value}
                    type="button"
                    onClick={() => switchMode(entry.value)}
                    aria-pressed={current}
                    // Named by the mode alone, so the eyebrow and the prose
                    // below do not end up read out as part of the name.
                    aria-label={entry.label}
                    aria-describedby={`mode-${entry.value}-description`}
                    className="group relative border-b border-paper/10 py-8 text-left last:border-b-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent md:border-b-0 md:px-10 md:first:pl-0 md:last:pr-0"
                  >
                    <span
                      className={`block text-[0.62rem] uppercase tracking-label transition-colors duration-200 ${
                        current ? 'text-accent' : 'text-paper-faint'
                      }`}
                    >
                      {entry.eyebrow}
                    </span>
                    <span
                      className={`mt-3 block font-display text-2xl font-light transition-colors duration-200 ${
                        current ? 'text-paper' : 'text-paper-dim group-hover:text-paper'
                      }`}
                    >
                      {entry.label}
                    </span>
                    <span
                      id={`mode-${entry.value}-description`}
                      className="mt-3 block max-w-sm text-[0.85rem] leading-relaxed text-paper-dim"
                    >
                      {entry.description}
                    </span>
                    {current && (
                      // The accent rule is the only mark of the current mode
                      // that is colour alone; `aria-pressed` carries it too.
                      <span
                        aria-hidden="true"
                        className="absolute -bottom-px left-0 h-px w-16 bg-accent"
                      />
                    )}
                  </button>
                )
              })}
            </div>
          </div>

          <div className="mx-auto max-w-page px-5 sm:px-6 lg:px-10">
            <div className="flex items-end gap-6 border-b border-paper/20 py-5 transition-colors duration-200 focus-within:border-accent">
              <label htmlFor="discover-query" className="sr-only">
                {active.inputLabel}
              </label>
              <input
                id="discover-query"
                type="search"
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder={active.placeholder}
                className="min-w-0 flex-1 bg-transparent font-display text-xl font-light text-paper placeholder:text-paper-faint focus-visible:outline-none md:text-2xl"
              />
              <button
                type="submit"
                className="group inline-flex shrink-0 items-center gap-2 border-b border-accent pb-1 text-[0.8rem] text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                Search
                <span
                  aria-hidden="true"
                  className="transition-transform duration-200 group-hover:translate-x-0.5"
                >
                  &rarr;
                </span>
              </button>
            </div>

            {facets && (
              <div className="flex flex-wrap items-end gap-x-10 gap-y-6 pt-8">
                <FacetSelect
                  id="filter-domain"
                  label="Medium"
                  allLabel="All media"
                  options={facets.domains}
                  value={filters.domain}
                  onChange={(value) => changeFilter('domain', value)}
                />
                {/*
                  Semantic search accepts a domain and nothing else, so the
                  two filters it cannot honour are not offered in that mode
                  rather than being offered and ignored.
                */}
                {mode === 'titles' && (
                  <>
                    <FacetSelect
                      id="filter-concept"
                      label="Theme"
                      allLabel="Any theme"
                      options={facets.concepts}
                      value={filters.concept}
                      onChange={(value) => changeFilter('concept', value)}
                    />
                    <FacetSelect
                      id="filter-genre"
                      label="Genre"
                      allLabel="Any genre"
                      options={facets.genres}
                      value={filters.genre}
                      onChange={(value) => changeFilter('genre', value)}
                    />
                  </>
                )}
                {filtered && (
                  <button
                    type="button"
                    onClick={clearEverything}
                    className="border-b border-paper/25 pb-1 text-[0.8rem] text-paper-dim transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    Clear filters
                  </button>
                )}
              </div>
            )}

            {mode === 'titles' && facets && facets.genres.length > 0 && (
              // Said plainly rather than left to be discovered by an empty
              // result: genres come from the source, and one source has none.
              <p className="max-w-2xl pb-10 pt-6 text-[0.8rem] leading-relaxed text-paper-faint">
                Genres come from the source that supplied each work, so literature
                works carry none. Themes are Noema’s own vocabulary and cover every
                medium.
              </p>
            )}
            {!(mode === 'titles' && facets && facets.genres.length > 0) && (
              <div className="pb-10" />
            )}
          </div>
        </form>
      </section>

      {/* --- results -------------------------------------------------- */}

      <section className="mx-auto max-w-page px-5 py-14 sm:px-6 md:py-16 lg:px-10">
        {error && (
          <StateMessage
            kind="error"
            title="Noema could not load these works."
            detail={error}
          />
        )}

        {loading && !error && (
          <StateMessage
            kind="loading"
            title={mode === 'titles' ? 'Looking…' : 'Comparing passages…'}
          />
        )}

        {/* --- lexical results ------------------------------------------- */}

        {mode === 'titles' && !loading && !error && results && (
          <div aria-labelledby="results-heading">
            <SectionHeading
              id="results-heading"
              label="Results"
              title={resultTitle}
              description={
                activeFilterNames.length > 0
                  ? `Filtered to ${activeFilterNames.join(' · ')}.`
                  : undefined
              }
              action={
                filtered ? { label: 'Clear filters', onClick: clearEverything } : undefined
              }
            />

            {total === 0 ? (
              <div className="mt-10">
                <StateMessage
                  kind="empty"
                  title={
                    query
                      ? 'No works matched your search.'
                      : 'No works match these filters.'
                  }
                  detail={
                    query
                      ? 'Try fewer words, or search by meaning instead of by title.'
                      : 'Try a different medium, theme or genre.'
                  }
                  action={
                    <button
                      type="button"
                      onClick={clearEverything}
                      className="border-b border-accent pb-0.5 text-[0.85rem] text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    >
                      Show everything
                    </button>
                  }
                />
              </div>
            ) : (
              <>
                <ul className="mt-12 grid grid-cols-2 gap-x-6 gap-y-12 sm:gap-x-8 md:gap-y-14 lg:grid-cols-4">
                  {results.items.map(({ work, user_state: state }, index) => (
                    <li key={work.id}>
                      <WorkEntry
                        work={work}
                        state={state}
                        onOpen={onOpenWork}
                        // The first row is above the fold on most screens.
                        priority={index < 4}
                        // Canonical facts sit above the rule; this is the
                        // reader's own half, and it is null when anonymous.
                        detail={state ? activityLine(state, work.domain.slug) : null}
                      />
                    </li>
                  ))}
                </ul>

                {total > PAGE_SIZE && (
                  <nav
                    aria-label="Pagination"
                    className="mt-16 flex flex-wrap items-center gap-6 border-t border-paper/10 pt-6"
                  >
                    <p className="text-[0.62rem] uppercase tracking-label text-paper-faint">
                      Showing {first}–{last} of {total}
                    </p>
                    <div className="ml-auto flex items-center gap-8">
                      <button
                        type="button"
                        disabled={page <= 1}
                        onClick={() => setPage((current) => Math.max(1, current - 1))}
                        className="group inline-flex items-center gap-2 border-b border-paper/20 pb-1 text-[0.8rem] text-paper-dim transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-40 disabled:hover:border-paper/20 disabled:hover:text-paper-dim"
                      >
                        <span
                          aria-hidden="true"
                          className="transition-transform duration-200 group-hover:-translate-x-0.5"
                        >
                          &larr;
                        </span>
                        Previous
                      </button>
                      <button
                        type="button"
                        disabled={last >= total}
                        onClick={() => setPage((current) => current + 1)}
                        className="group inline-flex items-center gap-2 border-b border-paper/20 pb-1 text-[0.8rem] text-paper-dim transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-40 disabled:hover:border-paper/20 disabled:hover:text-paper-dim"
                      >
                        Next
                        <span
                          aria-hidden="true"
                          className="transition-transform duration-200 group-hover:translate-x-0.5"
                        >
                          &rarr;
                        </span>
                      </button>
                    </div>
                  </nav>
                )}
              </>
            )}
          </div>
        )}

        {/* --- semantic results ------------------------------------------- */}

        {mode === 'meaning' && !loading && !error && (
          <div aria-labelledby="semantic-heading">
            <SectionHeading
              id="semantic-heading"
              label="Passages"
              title={
                !semantic
                  ? 'Search by meaning'
                  : semantic.hits.length === 0
                    ? 'No passages matched your search.'
                    : `Passages that read like “${semantic.query}”`
              }
            />

            {!semantic ? (
              <div className="mt-10">
                <StateMessage
                  kind="empty"
                  title="Describe what you are in the mood for."
                  detail="Noema compares your description with the text it holds — “a chase across a city”, “grief over someone who is gone”."
                />
              </div>
            ) : semantic.hits.length === 0 ? (
              <div className="mt-10">
                <StateMessage
                  kind="empty"
                  title="No passages matched your search."
                  detail="Try describing the situation differently, or search by title instead."
                />
              </div>
            ) : (
              <>
                <ul className="mt-12 divide-y divide-paper/10 border-t border-paper/10">
                  {semantic.hits.map((hit, index) => (
                    <li
                      key={`${hit.content_unit_id ?? hit.passage_id ?? index}`}
                      className="py-10"
                    >
                      <button
                        type="button"
                        onClick={() => onOpenWork(hit.work_id)}
                        className="text-left font-display text-2xl font-light text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                      >
                        {hit.work_title}
                      </button>

                      <p className="mt-3 text-[0.62rem] uppercase tracking-label text-paper-faint">
                        {hit.domain_slug}
                        {hit.container_title && ` · ${hit.container_title}`}
                      </p>

                      {/*
                        Which tier the matching text came from, always. A
                        third-party episode summary is not the episode, and
                        a reader must be able to tell.
                      */}
                      <p className="mt-2 text-[0.8rem] text-paper-faint">
                        {hit.text_tier === 'summary'
                          ? `— from a ${hit.source_name ?? 'third-party'} summary, not the work’s own text`
                          : '— from the work’s own text'}
                      </p>

                      <p className="mt-5 max-w-3xl font-display text-lg font-light italic leading-relaxed text-paper/85">
                        {hit.text_excerpt}
                      </p>
                    </li>
                  ))}
                </ul>

                <p className="mt-10 max-w-2xl text-[0.8rem] leading-relaxed text-paper-faint">
                  These are passages whose wording is close to your description. They
                  are not chosen for you, and closeness is not quality.{' '}
                  <button
                    type="button"
                    onClick={() =>
                      onOpenRetrievalDetail({ mode, query, page, ...filters })
                    }
                    className="border-b border-paper/25 text-paper-dim transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    Inspect retrieval details
                  </button>
                </p>
              </>
            )}
          </div>
        )}
      </section>
    </AppShell>
  )
}
