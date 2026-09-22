import { useState } from 'react'
import { semanticSearch } from '../api/search'
import type { SemanticSearchResponse } from '../types/api'

const DOMAINS = [
  { value: '', label: 'All domains' },
  { value: 'literature', label: 'Literature' },
  { value: 'anime', label: 'Anime' },
  { value: 'manhwa', label: 'Manga & Manhwa' },
]

const TIERS = [
  { value: '', label: 'All text' },
  { value: 'primary', label: 'Primary (original text)' },
  { value: 'summary', label: 'Summary (third-party)' },
]

const REPRESENTATIONS = [
  { value: 'content_unit', label: 'Source units' },
  { value: 'contextual_passage', label: 'Contextual passages' },
]

interface SemanticSearchProps {
  onBack: () => void
}

export default function SemanticSearch({ onBack }: SemanticSearchProps) {
  const [query, setQuery] = useState('')
  const [domain, setDomain] = useState('')
  const [tier, setTier] = useState('')
  const [representation, setRepresentation] = useState('content_unit')
  const [results, setResults] = useState<SemanticSearchResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function runSearch(event: React.FormEvent) {
    event.preventDefault()
    if (!query.trim()) return

    setLoading(true)
    setError(null)
    try {
      setResults(
        await semanticSearch({
          query,
          domain: domain || null,
          text_tier: tier || null,
          representation,
          top_k: 10,
        }),
      )
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed')
      setResults(null)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="border-b border-slate-800 px-6 py-4">
        <button
          type="button"
          onClick={onBack}
          className="mb-2 text-sm text-slate-400 hover:text-slate-200"
        >
          &larr; All works
        </button>
        {/*
          Deliberately technical, and deliberately labelled. This is the
          retrieval-inspection surface: renaming its vocabulary would make it
          useless for the one job it has. The banner is what keeps a reader
          who arrives here from mistaking it for the product -- Discover's
          "Search by theme" is the same retrieval, said for a reader.
        */}
        <p className="mb-2 inline-block border border-slate-700 px-2 py-0.5 text-[0.65rem] uppercase tracking-[0.18em] text-slate-400">
          Development surface
        </p>
        <h1 className="text-xl font-semibold tracking-tight">Semantic search</h1>
        <p className="text-sm text-slate-400">
          Vector similarity over embedded text. Results are computational observations, not
          stated relationships. The reader-facing version of this is “Search by
          theme” in Discover.
        </p>
      </header>

      <main className="mx-auto max-w-3xl px-6 py-8">
        <form onSubmit={runSearch} className="mb-6 space-y-3">
          <input
            id="semantic-query"
            type="text"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Describe a theme, scene, or idea&hellip;"
            className="w-full rounded-lg border border-slate-700 bg-slate-900 px-4 py-2 text-slate-100 placeholder:text-slate-500 focus:border-slate-500 focus:outline-none"
          />
          <div className="flex flex-wrap gap-3">
            <select
              id="semantic-domain"
              aria-label="Domain"
              value={domain}
              onChange={(event) => setDomain(event.target.value)}
              className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm"
            >
              {DOMAINS.map((option) => (
                <option key={option.label} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <select
              id="semantic-tier"
              aria-label="Text tier"
              value={tier}
              onChange={(event) => setTier(event.target.value)}
              className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm"
            >
              {TIERS.map((option) => (
                <option key={option.label} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <select
              id="semantic-representation"
              aria-label="Representation"
              value={representation}
              onChange={(event) => setRepresentation(event.target.value)}
              className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-sm"
            >
              {REPRESENTATIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            <button
              type="submit"
              disabled={loading || !query.trim()}
              className="rounded-lg border border-slate-600 bg-slate-800 px-4 py-2 text-sm hover:bg-slate-700 disabled:opacity-50"
            >
              {loading ? 'Searching…' : 'Search'}
            </button>
          </div>
        </form>

        {error && <p className="text-red-400">{error}</p>}

        {results && (
          <section data-testid="search-results">
            <p className="mb-4 text-xs text-slate-500">
              {results.hits.length} result(s) &middot; {results.metric} similarity &middot; model{' '}
              {results.model_name}
            </p>

            {/* Unfiltered searches compare the work's own prose against
                third-party summaries, which are not like for like. */}
            {results.text_tier === null && (
              <p className="mb-4 rounded border border-amber-900/50 bg-amber-950/30 p-3 text-xs text-amber-200/80">
                Mixed text tiers: Literature results are the work&rsquo;s own words, Anime
                results are third-party summaries. Filter by text tier to compare like with
                like.
              </p>
            )}

            {results.hits.length === 0 && (
              <p className="text-slate-400">No embedded text matched that query.</p>
            )}

            <ol className="space-y-4">
              {results.hits.map((hit) => (
                <li
                  // Passage hits have no content_unit_id, so key off whichever
                  // identifier this representation actually carries.
                  key={hit.content_unit_id ?? hit.passage_id}
                  className="rounded-lg border border-slate-800 bg-slate-900 p-4"
                >
                  <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                    <span className="font-medium text-slate-200">{hit.work_title}</span>
                    <span className="text-slate-500">
                      {/* A work-level unit sits in no container: it describes
                          the whole work, and inventing a number for it would
                          be a claim the source never made. */}
                      {hit.container_id === null
                        ? 'whole work'
                        : `${hit.container_title ?? hit.container_type} ${hit.container_sequence_number}`}
                    </span>
                    <span className="rounded bg-slate-800 px-2 py-0.5 uppercase tracking-wide text-slate-400">
                      {hit.domain_slug}
                    </span>
                    <span
                      className={`rounded px-2 py-0.5 uppercase tracking-wide ${
                        hit.text_tier === 'summary'
                          ? 'bg-amber-900/40 text-amber-300'
                          : 'bg-slate-800 text-slate-400'
                      }`}
                    >
                      {hit.text_tier}
                    </span>
                    {/* A contextual passage is derived, not something the
                        source contained; say so and show what produced it. */}
                    {hit.representation === 'contextual_passage' && (
                      <span className="rounded bg-sky-900/40 px-2 py-0.5 uppercase tracking-wide text-sky-300">
                        passage of {hit.unit_count} units
                      </span>
                    )}
                    <span className="ml-auto font-mono text-slate-400">
                      similarity {hit.similarity.toFixed(3)}
                    </span>
                  </div>
                  <p className="text-sm text-slate-300">{hit.text_excerpt}</p>
                  {hit.representation === 'contextual_passage' && (
                    <p className="mt-2 text-xs text-slate-500">
                      derived from source units {hit.first_unit_sequence}&ndash;
                      {hit.last_unit_sequence} &middot; {hit.grouping_config}
                    </p>
                  )}
                  {hit.source_name && (
                    <p className="mt-2 text-xs text-slate-500">
                      source: {hit.source_name}
                      {hit.licence ? ` · ${hit.licence}` : ''}
                    </p>
                  )}
                </li>
              ))}
            </ol>
          </section>
        )}
      </main>
    </div>
  )
}
