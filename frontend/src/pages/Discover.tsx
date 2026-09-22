import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import AppShell from '../components/AppShell'
import type { ProductView } from '../components/AppShell'
import SectionHeading from '../components/SectionHeading'
import { FolioBar } from '../components/Editorial'
import StateMessage from '../components/StateMessage'
import { fetchDiscoveryFacets, fetchWorks } from '../api/catalog'
import { workSearch } from '../api/search'
import { DEV_SURFACES } from '../lib/config'
import type {
  DiscoveryFacets,
  ProductWork,
  UserWorkState,
  WorkListResponse,
  WorkSearchHit,
  WorkSearchResponse,
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
 * Meaning results are works, like everything else here. The retrieval is
 * still passage-level -- that is what the vectors index -- but a novel that
 * matches in four places was arriving as four results, which is a list of
 * paragraphs rather than a list of works. The server folds the passages into
 * the works they came from and `top_k` counts works; the raw passages are
 * still available on the retrieval-inspection surface, which is where they
 * belong. Each result carries the one passage that scored highest, kept
 * short and behind a disclosure: enough to answer "why did this appear",
 * nowhere near enough to read the corpus from.
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
    label: 'By theme',
    eyebrow: 'By what happens',
    description:
      'Looks for writing that reads like what you describe. It is a way of finding things, not a recommendation — everyone describing the same thing sees the same works.',
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

/**
 * Why one work came back from a meaning search.
 *
 * The similarity, then -- only if asked -- where the strongest passage sits
 * and a short quotation from it. Compact and secondary on purpose: the
 * result is the work, and a list that printed passages would be a reading
 * interface for a corpus Noema does not redistribute.
 *
 * The tier is always stated. A match against a third-party summary is not a
 * match against the work's own words.
 */
function SemanticEvidence({ hit }: { hit: WorkSearchHit }) {
  const { evidence } = hit
  const where = [
    // No container means the passage describes the whole work, so say that
    // rather than printing a chapter number the source never gave us.
    evidence.container_id === null
      ? 'across the whole work'
      : (evidence.container_title ??
        `${evidence.container_type} ${evidence.container_sequence_number}`),
    evidence.text_tier === 'summary'
      ? `from a ${evidence.source_name ?? 'third-party'} summary`
      : 'from the work itself',
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <details>
      <summary className="type-label cursor-pointer text-paper-faint transition-colors duration-150 hover:text-accent-bright focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper">
        Why it appeared
      </summary>
      {/*
        The export sets a matched passage as a pulled quote against an accent
        rule, with "AFFINITY SCORE: 94.2%" beside it. The quote treatment is
        kept and the score is not: a cosine distance is an internal
        measurement, and printing it as a percentage invites a reader to
        compare two numbers that were never on the same scale.
      */}
      <div className="mt-3 border-l border-accent-bright/40 pl-4">
        <p className="type-label text-paper-faint">{where}</p>
        <blockquote className="mt-2 font-display text-[0.95rem] font-light italic leading-relaxed text-paper-dim">
          {evidence.excerpt}
        </blockquote>
        {evidence.matching_passages > 1 && (
          <p className="type-label mt-2 text-paper-faint">
            {evidence.matching_passages} passages matched
          </p>
        )}
      </div>
    </details>
  )
}

/**
 * One result, as the export sets them out.
 *
 * Not a card in a grid. The Stitch catalogue gives every result the full width
 * of the page and divides it four columns to eight: the record's identity on
 * the left, what was actually found on the right, separated by a vertical
 * rule. Results are stacked with a hairline between them, so a page of them
 * reads as a register rather than as a wall of tiles.
 *
 * Both search modes use it, because the shape is the same question answered
 * two ways. In title mode the right half carries the work's own synopsis; in
 * theme mode it carries the passage that actually matched, set as a pulled
 * quote with a note saying where in the work it came from.
 *
 * **No score.** The export prints `AFFINITY SCORE: 94.2%` in the corner of
 * every one of these, and the bottom of its own page promises that no
 * retrieval scores are exposed. Noema keeps the promise instead: `similarity`
 * and `distance` arrive on every hit and neither is rendered, because a
 * cosine distance is a measurement of the index rather than a statement about
 * the work, and a percentage invites a reader to compare two numbers that
 * were never on the same scale.
 */
function ResultArticle({
  work,
  state,
  excerpt,
  onOpenWork,
}: {
  work: ProductWork
  state: UserWorkState | null
  /** The matched passage, in theme mode. Absent in title mode. */
  excerpt?: ReactNode
  onOpenWork: (workId: string) => void
}) {
  const credited = work.creators
    .slice(0, 2)
    .map((creator) => creator.name)
    .join(', ')

  return (
    <li>
      <article className="grid gap-x-10 gap-y-5 border-b border-paper/10 py-6 transition-colors duration-150 hover:bg-canvas-soft/60 md:py-7 lg:grid-cols-12">
        {/* --- the record ---------------------------------------------- */}
        <div className="lg:col-span-4">
          <p className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="type-label border border-paper/20 px-2 py-1 text-accent-bright">
              {work.domain.name}
            </span>
            <span className="type-num text-paper-faint">
              {[work.media_format, work.year].filter(Boolean).join(' · ')}
            </span>
          </p>

          <h3 className="mt-4">
            <button
              type="button"
              onClick={() => onOpenWork(work.id)}
              aria-label={`${work.title} — open this work`}
              className="text-left font-display text-2xl font-light leading-tight text-paper transition-colors duration-150 hover:text-accent-bright focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper"
            >
              {work.title}
            </button>
          </h3>
          {credited && <p className="type-body-sm mt-2 text-paper-faint">{credited}</p>}

          {/*
            The export's inset registry block: the original title and the
            record's own filing line, framed. Only rendered when the record
            actually carries an original title — an empty frame is furniture.
          */}
          {work.original_title && work.original_title !== work.title && (
            <div className="mt-5 border border-paper/10 bg-ink p-4">
              <p className="font-display text-base font-light italic text-paper-dim">
                {work.original_title}
              </p>
            </div>
          )}

          {state?.in_library && (
            <p className="type-label mt-4 text-paper-dim">
              In your library
              {state.rating !== null && ` · rated ${state.rating}/10`}
            </p>
          )}
        </div>

        {/* --- what was found ------------------------------------------ */}
        <div className="border-t border-paper/10 pt-6 lg:col-span-8 lg:border-l lg:border-t-0 lg:pl-10 lg:pt-0">
          {excerpt ?? (
            <>
              <p className="type-label text-paper-faint">Synopsis</p>
              {work.synopsis ? (
                /*
                  Clamped in the register, never on the work page.
                  
                  A full synopsis runs to a dozen lines, and twelve of them
                  stacked made the results page four screens longer than the
                  results themselves. Here it is an identifying paragraph and
                  four lines is enough to recognise a work; the dossier is
                  one click away and sets it in full, which is what that page
                  is for.
                */
                <p className="mt-3 line-clamp-4 max-w-3xl font-display text-lg font-light leading-relaxed text-paper-dim">
                  {work.synopsis}
                </p>
              ) : (
                <p className="mt-3 font-display text-lg font-light italic text-paper-faint">
                  No synopsis was supplied with this record.
                </p>
              )}
            </>
          )}

          {work.concepts.length > 0 && (
            <div className="mt-4">
              <p className="type-label text-paper-faint">Themes</p>
              <ul className="mt-2 flex flex-wrap gap-2">
                {work.concepts.slice(0, 5).map((concept) => (
                  <li
                    key={concept.slug}
                    className="type-label border border-paper/15 px-2 py-1 text-paper-dim"
                  >
                    {concept.name}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <p className="mt-5">
            <button
              type="button"
              onClick={() => onOpenWork(work.id)}
              className="type-label group inline-flex items-center gap-2 border border-paper/25 px-4 py-2.5 text-paper transition-colors duration-150 hover:border-accent-bright hover:text-accent-bright focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper"
            >
              Open this work
              <span
                aria-hidden="true"
                className="transition-transform duration-150 group-hover:translate-x-0.5"
              >
                &rarr;
              </span>
            </button>
          </p>
        </div>
      </article>
    </li>
  )
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
  const [semantic, setSemantic] = useState<WorkSearchResponse | null>(null)
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
        await workSearch({
          query,
          // Unique works, not raw passages: the server widens the candidate
          // pool itself to make that possible.
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
      folio={
        <FolioBar
          left={
            <span className="type-label text-accent-bright">
              Index // lexical and thematic
            </span>
          }
          /*
            The export puts "LATENCY: 0.04S · TEMPERATURE: 0.12 · MATCH
            THRESHOLD: >= 0.81" here. None of those is a fact about the
            catalogue and two of them are invented; what belongs on a registry
            strip is how much is in the index, which is real and which the
            facets already carry.
          */
          right={
            corpusLine(facets) ? (
              <span className="type-num text-paper-faint">{corpusLine(facets)}</span>
            ) : undefined
          }
        />
      }
      masthead={
        <div className="border-b border-paper/10">
          <div className="mx-auto grid max-w-page gap-x-10 gap-y-5 px-5 py-7 sm:px-6 md:grid-cols-12 md:items-end md:py-9 lg:px-10">
            <div className="md:col-span-8">
              <p className="type-label text-paper-faint">The collection</p>
              <h1 className="type-display mt-4 text-paper lg:text-[3.6rem] lg:leading-[1.05]">
                Discover
              </h1>
            </div>
            <div className="border-t border-paper/10 pt-6 md:col-span-4 md:border-l md:border-t-0 md:pl-8 md:pt-0">
              <p className="type-body text-paper-dim">
                Everything Noema holds, across literature, anime and manga — search it
                by title, or describe a theme and see what reads like it.
              </p>
              {/*
                Per-medium counts, straight from `/works/facets`: the same
                figures the filters below narrow to, so a reader who filters
                finds the number they were shown.
              */}
              {facets?.domains && facets.domains.length > 0 && (
                <dl className="mt-6 grid grid-cols-3 gap-4 border-t border-paper/10 pt-4">
                  {facets.domains.map((entry) => (
                    <div key={entry.value}>
                      <dt className="type-label text-paper-faint">{entry.label}</dt>
                      <dd className="type-num mt-1.5 text-paper-dim">{entry.count}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </div>
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
                    className="group relative border-b border-paper/10 py-5 text-left last:border-b-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent md:border-b-0 md:px-10 md:first:pl-0 md:last:pr-0"
                  >
                    <span
                      className={`type-label block transition-colors duration-150 ${
                        current ? 'text-accent-bright' : 'text-paper-faint'
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

      <section className="mx-auto max-w-page px-5 py-8 sm:px-6 md:py-10 lg:px-10">
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
                <ul className="mt-6 border-t border-paper/10">
                  {results.items.map(({ work, user_state: state }) => (
                    <ResultArticle
                      key={work.id}
                      work={work}
                      state={state}
                      onOpenWork={onOpenWork}
                    />
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
              label="By theme"
              title={
                !semantic
                  ? 'Search by theme'
                  : semantic.results.length === 0
                    ? 'Nothing here reads like that.'
                    : `Works that read like “${semantic.query}”`
              }
            />

            {!semantic ? (
              <div className="mt-10">
                <StateMessage
                  kind="empty"
                  title="Describe what you are in the mood for."
                  detail="Noema looks through the writing it holds for something that reads like it — “a chase across a city”, “grief over someone who is gone”."
                />
              </div>
            ) : semantic.results.length === 0 ? (
              <div className="mt-10">
                <StateMessage
                  kind="empty"
                  title="Nothing here reads like that."
                  detail="Try describing the situation differently, or search by title instead."
                />
              </div>
            ) : (
              <>
                <ul className="mt-6 border-t border-paper/10">
                  {semantic.results.map((hit) => (
                    <ResultArticle
                      key={hit.work.id}
                      work={hit.work}
                      state={hit.user_state}
                      onOpenWork={onOpenWork}
                      excerpt={<SemanticEvidence hit={hit} />}
                    />
                  ))}
                </ul>

                <p className="mt-10 max-w-2xl text-[0.8rem] leading-relaxed text-paper-faint">
                  These are works whose writing reads like your description. They
                  are not chosen for you, and reading alike is not the same as
                  being good.
                  {/*
                    The inspector behind this link shows distances and
                    representation names. It exists in a development build
                    only, so the link does too -- a production bundle has no
                    route to send anyone to.
                  */}
                  {DEV_SURFACES && (
                    <>
                      {' '}
                      <button
                        type="button"
                        onClick={() =>
                          onOpenRetrievalDetail({ mode, query, page, ...filters })
                        }
                        className="border-b border-paper/25 text-paper-dim transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                      >
                        See how this search works
                      </button>
                    </>
                  )}
                </p>
              </>
            )}
          </div>
        )}
      </section>
    </AppShell>
  )
}
