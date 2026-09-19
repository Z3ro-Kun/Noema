import { useCallback, useEffect, useState } from 'react'
import AppShell from '../components/AppShell'
import type { ProductView } from '../components/AppShell'
import StateMessage from '../components/StateMessage'
import WorkCard from '../components/WorkCard'
import { fetchDiscoveryFacets, fetchWorks } from '../api/catalog'
import { semanticSearch } from '../api/search'
import type {
  DiscoveryFacets,
  SemanticSearchResponse,
  WorkListResponse,
} from '../types/api'

/**
 * Discover -- browsing and searching the canonical corpus.
 *
 * Phase 1Y. The product's answer to "what is in Noema", and deliberately not
 * its answer to "what should I read next".
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
 */

const PAGE_SIZE = 12
const SEMANTIC_TOP_K = 10

type Mode = 'titles' | 'meaning'

interface DiscoverProps {
  /** A filter chosen elsewhere -- a domain chip on Home, a taste shelf. */
  initialDomain?: string
  initialConcept?: string
  onNavigate: (view: ProductView) => void
  onOpenWork: (workId: string) => void
  /** The retrieval-inspection surface, for the curious and for debugging. */
  onOpenRetrievalDetail: () => void
  account: string | null
}

interface Filters {
  domain: string
  concept: string
  genre: string
}

const NO_FILTERS: Filters = { domain: '', concept: '', genre: '' }

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
    <label htmlFor={id} className="block text-xs text-slate-400">
      {label}
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-900 px-2 py-1.5 text-sm text-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300 sm:w-48"
      >
        <option value="">{allLabel}</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label} ({option.count})
          </option>
        ))}
      </select>
    </label>
  )
}

export default function Discover({
  initialDomain = '',
  initialConcept = '',
  onNavigate,
  onOpenWork,
  onOpenRetrievalDetail,
  account,
}: DiscoverProps) {
  const [mode, setMode] = useState<Mode>('titles')
  // What is typed, and what was actually searched for. Separate, so the
  // results heading cannot describe a query that was never sent.
  const [draft, setDraft] = useState('')
  const [query, setQuery] = useState('')
  const [filters, setFilters] = useState<Filters>({
    ...NO_FILTERS,
    domain: initialDomain,
    concept: initialConcept,
  })
  const [page, setPage] = useState(1)

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

  const filtered = Boolean(filters.domain || filters.concept || filters.genre || query)
  const total = results?.total ?? 0
  const first = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1
  const last = Math.min(page * PAGE_SIZE, total)

  return (
    <AppShell
      title="Discover"
      subtitle="Everything Noema holds, across literature, anime and manga."
      current="discover"
      onNavigate={onNavigate}
      actions={
        account ? (
          <p className="hidden text-xs text-slate-500 sm:block">{account}</p>
        ) : null
      }
    >
      <div className="space-y-6">
        <form onSubmit={submit} className="space-y-3">
          <div
            role="group"
            aria-label="Search mode"
            className="flex flex-wrap gap-1 rounded-lg border border-slate-800 p-1"
          >
            {(
              [
                ['titles', 'Titles'],
                ['meaning', 'By meaning'],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => switchMode(value)}
                aria-pressed={mode === value}
                className={`rounded px-3 py-1.5 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300 ${
                  mode === value
                    ? 'bg-slate-800 text-slate-100'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          <p className="text-xs text-slate-500">
            {mode === 'titles'
              ? 'Looks for the words you type in titles. Nothing is personalised — everyone searching the same thing sees the same works.'
              : 'Looks for passages that mean something similar, using Noema’s embeddings. Results are text similarity, not a recommendation.'}
          </p>

          <div className="flex flex-wrap gap-2">
            <label htmlFor="discover-query" className="sr-only">
              {mode === 'titles' ? 'Search titles' : 'Describe a theme'}
            </label>
            <input
              id="discover-query"
              type="search"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              placeholder={
                mode === 'titles' ? 'Search titles…' : 'Describe a theme or situation…'
              }
              className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
            />
            <button
              type="submit"
              className="rounded-lg border border-slate-600 bg-slate-800 px-4 py-2 text-sm text-slate-100 hover:border-slate-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
            >
              Search
            </button>
          </div>

          {facets && (
            <div className="flex flex-wrap gap-3">
              <FacetSelect
                id="filter-domain"
                label="Medium"
                allLabel="All media"
                options={facets.domains}
                value={filters.domain}
                onChange={(value) => changeFilter('domain', value)}
              />
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
                  onClick={() => {
                    setFilters(NO_FILTERS)
                    setDraft('')
                    setQuery('')
                    setPage(1)
                  }}
                  className="self-end rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-400 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
                >
                  Clear filters
                </button>
              )}
            </div>
          )}

          {mode === 'titles' && facets && facets.genres.length > 0 && (
            // Said plainly rather than left to be discovered by an empty
            // result: genres come from the source, and one source has none.
            <p className="text-xs text-slate-600">
              Genres come from the source that supplied each work, so literature
              works carry none. Themes are Noema’s own vocabulary and cover every
              medium.
            </p>
          )}
        </form>

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
          <section aria-labelledby="results-heading" className="space-y-4">
            <h2 id="results-heading" className="text-sm font-medium text-slate-300">
              {total === 0
                ? 'No works'
                : `${total} ${total === 1 ? 'work' : 'works'}`}
              {query && ` matching “${query}”`}
            </h2>

            {total === 0 ? (
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
                    onClick={() => {
                      setFilters(NO_FILTERS)
                      setDraft('')
                      setQuery('')
                      setPage(1)
                    }}
                    className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-200 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
                  >
                    Show everything
                  </button>
                }
              />
            ) : (
              <>
                <ul className="space-y-3">
                  {results.items.map(({ work, user_state: state }) => (
                    <li key={work.id}>
                      <WorkCard work={work} state={state} onOpen={onOpenWork} />
                    </li>
                  ))}
                </ul>

                {total > PAGE_SIZE && (
                  <nav
                    aria-label="Pagination"
                    className="flex flex-wrap items-center gap-3 border-t border-slate-800 pt-4"
                  >
                    <p className="text-xs text-slate-500">
                      Showing {first}–{last} of {total}
                    </p>
                    <div className="ml-auto flex gap-2">
                      <button
                        type="button"
                        disabled={page <= 1}
                        onClick={() => setPage((current) => Math.max(1, current - 1))}
                        className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300 disabled:opacity-40"
                      >
                        Previous
                      </button>
                      <button
                        type="button"
                        disabled={last >= total}
                        onClick={() => setPage((current) => current + 1)}
                        className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300 disabled:opacity-40"
                      >
                        Next
                      </button>
                    </div>
                  </nav>
                )}
              </>
            )}
          </section>
        )}

        {/* --- semantic results ------------------------------------------- */}

        {mode === 'meaning' && !loading && !error && (
          <section aria-labelledby="semantic-heading" className="space-y-4">
            <h2 id="semantic-heading" className="text-sm font-medium text-slate-300">
              {!semantic
                ? 'Search by meaning'
                : semantic.hits.length === 0
                  ? 'No passages matched your search.'
                  : `Passages that read like “${semantic.query}”`}
            </h2>

            {!semantic ? (
              <StateMessage
                kind="empty"
                title="Describe what you are in the mood for."
                detail="Noema compares your description with the text it holds — “a chase across a city”, “grief over someone who is gone”."
              />
            ) : semantic.hits.length === 0 ? (
              <StateMessage
                kind="empty"
                title="No passages matched your search."
                detail="Try describing the situation differently, or search by title instead."
              />
            ) : (
              <>
                <ul className="space-y-3">
                  {semantic.hits.map((hit, index) => (
                    <li
                      key={`${hit.content_unit_id ?? hit.passage_id ?? index}`}
                      className="rounded-lg border border-slate-800 p-4"
                    >
                      <button
                        type="button"
                        onClick={() => onOpenWork(hit.work_id)}
                        className="text-left font-medium text-slate-100 hover:text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
                      >
                        {hit.work_title}
                      </button>
                      <p className="mt-0.5 text-xs text-slate-500">
                        {hit.domain_slug}
                        {hit.container_title && ` · ${hit.container_title}`}
                        {/*
                          Which tier the matching text came from, always. A
                          third-party episode summary is not the episode, and
                          a reader must be able to tell.
                        */}
                        {hit.text_tier === 'summary'
                          ? ` · from a ${hit.source_name ?? 'third-party'} summary, not the work’s own text`
                          : ' · from the work’s own text'}
                      </p>
                      <p className="mt-2 text-sm text-slate-400">{hit.text_excerpt}</p>
                    </li>
                  ))}
                </ul>
                <p className="text-xs text-slate-600">
                  These are passages whose wording is close to your description. They
                  are not chosen for you, and closeness is not quality.{' '}
                  <button
                    type="button"
                    onClick={onOpenRetrievalDetail}
                    className="underline hover:text-slate-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
                  >
                    Inspect retrieval details
                  </button>
                </p>
              </>
            )}
          </section>
        )}
      </div>
    </AppShell>
  )
}
