import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import AppShell from '../components/AppShell'
import type { ProductView } from '../components/AppShell'
import SectionHeading from '../components/SectionHeading'
import { Chip, SectionMarker } from '../components/Editorial'
import WorkLedgerRow, { LedgerHead } from '../components/WorkLedgerRow'
import StateMessage from '../components/StateMessage'
import WorkEntry from '../components/WorkEntry'
import WorkPlate from '../components/WorkPlate'
import { fetchDiscoveryFacets, fetchWorks } from '../api/catalog'
import { fetchTasteDashboard } from '../api/dashboard'
import { dismissRecommendation, fetchRecommendations } from '../api/recommendations'
import { addToLibrary, fetchLibrary, fetchLibrarySummary } from '../api/library'
import { useSession } from '../auth/session'
import { statusLabel } from '../lib/labels'
import { hedge, leadPhrase, supportLine } from '../lib/taste'
import type {
  DiscoveryFacets,
  LibrarySummary,
  PreferenceBucket,
  Recommendation,
  RecommendationReason,
  RecommendationResponse,
  TasteDashboard,
  TastePreferenceItem,
  WorkPresentation,
} from '../types/api'

/**
 * Home -- what a reader can do in Noema right now.
 *
 * Built on the Magic Patterns composition: sticky masthead, a full-bleed
 * feature, a shelf of what is still open, a cross-medium thread, a light
 * taste band, three entry points, a footer. The rhythm, the section heights
 * and the visual mass are the prototype's; every value in them is Noema's.
 *
 * ---
 *
 * Imagery, and what is honest
 *
 * The prototype's hero was a photograph of the featured work and its shelves
 * were grids of cover art. Noema has no artwork -- `cover_image_url` is null
 * for every work -- and inventing some is the thing the product refuses.
 *
 * The first attempt at this page concluded that meant removing the pictures,
 * which removed the composition with them. It does not mean that. Two things
 * are different:
 *
 *     the hero background   decorative page furniture, in CSS -- an ink wash,
 *                           a vignette and grain. It depicts nothing and is
 *                           attached to no work, so nothing can be mistaken
 *                           for a cover. The eyebrow names the *theme*, never
 *                           "featured artwork".
 *     the shelf plates      the 2:3 slot survives, filled by a typographic
 *                           plate (`WorkPlate`) rather than a photograph.
 *                           Real artwork drops into the same slot later
 *                           without a layout change.
 *
 * ---
 *
 * What the prototype asserted that is not true here
 *
 *     reading position      no such field exists -- no "Episode 7 of 12", no
 *                           progress hairline. What is stored is a status,
 *                           said as news: "Currently watching".
 *     a strength bar        Phase 1W removed every numeric preference value
 *                           from the contract so nothing could be drawn as a
 *                           length. Groups are words; confidence is a word.
 *     "shares three         a recommendation claim. There is no recommender.
 *      concepts with..."     The feature names the theme it carries and says
 *                           the same works come from a Discover filter.
 *     a reader's name       `display_name` is never set.
 *     a month-scoped count  not exposed; the masthead shows lifetime totals.
 *     "12,480 works"        corpus size is an administration fact, not a
 *                           reader's headline. It is not on this page.
 *
 * ---
 *
 * The add action respects the auth boundary
 *
 * `POST /library` answers an anonymous caller with 401 and
 * `WWW-Authenticate: Bearer`, so the server cannot be tricked. The UI does
 * not try: signed out, the hero offers the sign-in panel rather than an
 * action that would fail. No request is made and no success is implied.
 */

const RECENT_LIMIT = 5
const SHELF_SIZE = 4
// Four across on a wide screen, which is one row and not a page of them.
const RECOMMENDATION_LIMIT = 4
/** At most two shelves: three would be a feed, and this is a landing page. */
const SHELF_COUNT = 2
/** Three rows is a band; more is a report. */
const TASTE_ROWS = 3

const DOMAINS = [
  { slug: 'literature', label: 'Literature' },
  { slug: 'anime', label: 'Anime' },
  { slug: 'manhwa', label: 'Manga & Manhwa' },
]

interface HomeProps {
  onNavigate: (view: ProductView) => void
  onOpenWork: (workId: string) => void
  /** Opens Discover with a filter already applied. */
  onExplore: (filter: { domain?: string; concept?: string }) => void
}

interface Shelf {
  concept: string
  label: string
  works: WorkPresentation[]
}

interface TasteRow {
  key: string
  /** The lead phrase: "You particularly enjoy". */
  lead: string
  name: string
  /** What it rests on, in a sentence of the reader's own ratings. */
  support: string
  /** A caveat, only where one is warranted. Usually null. */
  hedge: string | null
}

/** The established preferences a concept shelf can be built from. */
function shelfCandidates(dashboard: TasteDashboard): TastePreferenceItem[] {
  return [...dashboard.strongly_likes, ...dashboard.mildly_likes]
    // A combination is two concepts and Discover filters on one. Rather than
    // silently dropping half a finding, pairs are left to the profile page.
    .filter((item) => item.kind === 'individual' && item.features.length === 1)
    .slice(0, SHELF_COUNT)
}

/**
 * The sentence under a recommended work.
 *
 * `presentation_key` is a controlled key and the backend sends no prose, so
 * the wording is chosen here -- and it adds no fact. Every name in the
 * sentence is a concept the work actually carries and a preference the
 * reader can find on their own taste profile.
 */
function reasonSentence(reason: RecommendationReason): string {
  const names = reason.concepts.map((concept) => concept.name)
  switch (reason.presentation_key) {
    case 'enjoys_combination':
      return `Because you enjoy ${names.join(' with ')}`
    case 'negative_combination':
      return `Although ${names.join(' with ')} tends not to work for you`
    case 'negative_feature':
      return `Although ${names.join(' and ')} tends not to work for you`
    default:
      return `Because you enjoy ${names.join(' and ')}`
  }
}

/**
 * What the reason rests on, in the reader's own ratings.
 *
 * A count they can check against their own profile, not a grade. The band is
 * allowed to add "early days" and nothing more -- see `lib/taste` for why a
 * confidence word never appears on a public surface.
 */
function supportNote(reason: RecommendationReason): string {
  const works = plural(reason.rated_works, 'work', 'works')
  return reason.confidence_band === 'low'
    ? `From ${works} you rated — early days`
    : `From ${works} you rated`
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`
}

/**
 * The taste band's rows, from the real dashboard groups.
 *
 * The prototype listed three patterns with a strength bar each. The groups
 * and the supporting sentence survive; the bar does not, because no numeric
 * strength crosses the API and a length would read as a percentage.
 *
 * The wording is not chosen here -- `lib/taste` owns it, so this band and the
 * profile page say the same thing about the same finding.
 */
function tasteRows(dashboard: TasteDashboard | null): TasteRow[] {
  if (!dashboard) return []
  const groups: [PreferenceBucket, TastePreferenceItem[]][] = [
    ['strongly_likes', dashboard.strongly_likes],
    ['mildly_likes', dashboard.mildly_likes],
    ['dislikes', dashboard.dislikes],
    ['emerging', dashboard.emerging],
  ]

  const rows: TasteRow[] = []
  for (const [bucket, items] of groups) {
    for (const item of items) {
      rows.push({
        key: item.key,
        lead: leadPhrase(bucket),
        name: item.display_name,
        support: supportLine(item.evidence_summary),
        hedge: hedge(item.confidence_band, bucket),
      })
    }
  }
  return rows.slice(0, TASTE_ROWS)
}

/* -------------------------------------------------------------------------
 * Masthead
 * ---------------------------------------------------------------------- */

function Masthead({
  heading,
  standfirst,
  stats,
}: {
  heading: string
  standfirst: string
  stats?: { label: string; value: number }[]
}) {
  const date = new Date().toLocaleDateString(undefined, {
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  })

  return (
    <div className="mx-auto max-w-page px-5 pb-5 pt-5 sm:px-6 md:pb-6 md:pt-6 lg:px-10">
      {/*
        The broadsheet split: eight columns of headline, four of standfirst and
        register, divided by a rule rather than by space. The second column is
        laid out only when there is something true to put in it -- reserving it
        unconditionally left half the masthead empty.
      */}
      <div
        className={`grid gap-x-10 gap-y-8 ${
          stats && stats.length > 0 ? 'md:grid-cols-12 md:items-end' : ''
        }`}
      >
        <div className="md:col-span-8">
          <p className="type-label flex flex-wrap items-center gap-x-3 gap-y-1 text-paper-faint">
            <span aria-hidden="true" className="h-1.5 w-1.5 bg-accent-bright" />
            Personal registry
            <span aria-hidden="true" className="text-paper-faint/50">
              /
            </span>
            <span className="type-num normal-case tracking-normal">{date}</span>
          </p>
          <h1 className="type-display mt-3 max-w-3xl text-paper lg:text-[3rem] lg:leading-[1.05]">
            {heading}
          </h1>
        </div>

        {/*
          Standfirst and register share the right column, separated by a rule.
          The two counts are read straight from /library/summary; there is no
          month-scoped count exposed, so they are lifetime totals and say so.
        */}
        <div className="border-t border-paper/10 pt-6 md:col-span-4 md:border-l md:border-t-0 md:pl-8 md:pt-0">
          <p className="type-body text-paper-dim">{standfirst}</p>
          {stats && stats.length > 0 && (
            <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-2 border-t border-paper/10 pt-3">
              {stats.map((stat) => (
                <div key={stat.label} className="flex items-baseline gap-2">
                  <dt className="type-label text-paper-faint">{stat.label}</dt>
                  <dd className="font-display text-2xl font-light tabular-nums leading-none text-paper">
                    {stat.value}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      </div>
    </div>
  )
}


/* -------------------------------------------------------------------------
 * The feature
 * ---------------------------------------------------------------------- */

function Feature({
  entry,
  eyebrow,
  mechanism,
  onOpenWork,
  action,
}: {
  entry: WorkPresentation
  eyebrow: string
  mechanism?: string
  onOpenWork: (workId: string) => void
  /** The second hero action, which differs by auth state. */
  action: ReactNode
}) {
  const { work, user_state: state } = entry
  const credited = work.creators
    .slice(0, 2)
    .map((creator) => creator.name)
    .join(', ')

  return (
    <section aria-labelledby="feature-heading" className="border-b border-paper/10">
      {/*
        Tightened deliberately. The plate is a 2:3 portrait, and at five of
        twelve columns it set the height of the whole block -- so on a laptop
        a reader had to scroll before they could see the feature they had
        been shown the head of. The plate is now capped and centred in its
        column, the padding is closer, and the synopsis is clamped, which
        brings the block inside a normal viewport without changing what it is.
      */}
      <div className="mx-auto max-w-page px-5 py-6 sm:px-6 md:py-7 lg:px-10">
        <SectionMarker index="01" label={eyebrow} folio="Featured" />

        {/*
          The asymmetric split the export uses for its plate showcase: five
          columns of specimen, seven of rationale, divided by a rule rather
          than by a gap. Both halves sit inside one hairline frame, so the
          whole thing reads as a single mounted plate.
        */}
        <div className="mt-5 grid border border-paper/10 bg-ink lg:grid-cols-12">
          {/*
            --- the plate ------------------------------------------------

            The artwork fills its column edge to edge rather than floating in
            the middle of one. Two passes ago this plate set the height of the
            whole feature and pushed it past the fold; the fix then was to
            shrink the cover, which produced the opposite fault -- a thumbnail
            adrift in a large empty box.

            Height is controlled by the column instead. The image keeps its
            2:3 ratio and takes the full width it is given, and the block is
            kept short by the *number of columns* the plate occupies, by tight
            padding, and by the type beside it -- never by making the artwork
            smaller. There is no inner frame and no padding around it: the
            column edge is the frame, which is how a printed plate sits.
          */}
          <div className="border-b border-paper/10 bg-ink-soft lg:col-span-3 lg:border-b-0 lg:border-r">
            <button
              type="button"
              onClick={() => onOpenWork(work.id)}
              aria-label={`${work.title} — open this work`}
              className="block w-full max-w-[16rem] lg:max-w-none focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent"
            >
              <WorkPlate work={work} priority />
            </button>
          </div>

          {/* --- the rationale -------------------------------------------- */}
          <div className="flex flex-col gap-5 p-5 md:p-7 lg:col-span-9">
            <div>
              {/*
                Chips say only what is true of this reader and this work: the
                medium, and — when they hold it — their own status and rating.
                Nothing here is a score.
              */}
              <div className="flex flex-wrap items-center gap-2">
                <Chip>{work.domain.name}</Chip>
                {state?.in_library && <Chip tone="stated">In your library</Chip>}
                {state && <Chip tone="stated">{statusLabel(state.status)}</Chip>}
                {state?.rating != null && <Chip tone="marked">Rated {state.rating}/10</Chip>}
              </div>

              <h2
                id="feature-heading"
                className="type-headline-lg mt-5 text-paper md:text-[2.2rem] md:leading-[1.1]"
              >
                {work.title}
              </h2>
              {work.original_title && work.original_title !== work.title && (
                <p className="mt-2 font-display text-lg font-light italic text-paper-dim">
                  {work.original_title}
                </p>
              )}

              {work.synopsis ? (
                <blockquote className="mt-4 line-clamp-2 border-l border-accent-bright/40 pl-5 font-display text-lg font-light leading-relaxed text-paper">
                  {work.synopsis}
                </blockquote>
              ) : (
                // Gutenberg states no description, so literature has none.
                // Said in words rather than left as a gap in the composition.
                <p className="mt-6 border-l border-paper/15 pl-5 font-display text-lg font-light italic leading-relaxed text-paper-faint">
                  No synopsis was supplied with this record.
                </p>
              )}

              {mechanism && (
                <p className="type-body mt-5 max-w-xl text-paper-dim">{mechanism}</p>
              )}
            </div>

            {/* --- what the record actually holds ------------------------ */}
            <div>
              {/*
                One ruled line rather than a grid of cells. Four stacked
                label-over-value blocks wrapped to two rows here and pushed
                the whole feature past the fold; the same four facts read
                just as well inline, and the feature fits a laptop.
              */}
              <dl className="flex flex-wrap items-baseline gap-x-8 gap-y-2 border-t border-paper/10 pt-4">
                {[
                  credited && { label: 'Credited', value: credited },
                  work.media_format && { label: 'Format', value: work.media_format },
                  work.year !== null && { label: 'Year', value: String(work.year) },
                  work.concepts.length > 0 && {
                    label: 'Themes',
                    value: work.concepts
                      .slice(0, 3)
                      .map((concept) => concept.name)
                      .join(' · '),
                  },
                ]
                  .filter((entry): entry is { label: string; value: string } => Boolean(entry))
                  .map((entry) => (
                    <div key={entry.label} className="flex items-baseline gap-2">
                      <dt className="type-label text-paper-faint">{entry.label}</dt>
                      <dd className="type-body text-paper">{entry.value}</dd>
                    </div>
                  ))}
              </dl>

              <div className="mt-6 flex flex-wrap items-center gap-x-8 gap-y-4">
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

                {state && state.in_library ? (
                  <p className="type-body text-paper-dim">
                    In your library
                    {state.rating !== null && ` · rated ${state.rating}/10`}
                  </p>
                ) : (
                  action
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

/* -------------------------------------------------------------------------
 * Home
 * ---------------------------------------------------------------------- */

export default function Home({ onNavigate, onOpenWork, onExplore }: HomeProps) {
  const session = useSession()

  const [recent, setRecent] = useState<WorkPresentation[]>([])
  const [dashboard, setDashboard] = useState<TasteDashboard | null>(null)
  const [summary, setSummary] = useState<LibrarySummary | null>(null)
  const [facets, setFacets] = useState<DiscoveryFacets | null>(null)
  const [shelves, setShelves] = useState<Shelf[]>([])
  const [suggested, setSuggested] = useState<RecommendationResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)

  const loadForReader = useCallback(async () => {
    setLoading(true)
    try {
      // The library is the only required call. A taste profile or a summary
      // that fails must not take the page with it -- the reader's own history
      // is still worth showing.
      const [library, profile, counts, recommended, index] = await Promise.all([
        fetchLibrary({ page_size: RECENT_LIMIT }),
        fetchTasteDashboard().catch(() => null),
        fetchLibrarySummary().catch(() => null),
        fetchRecommendations(RECOMMENDATION_LIMIT).catch(() => null),
        fetchDiscoveryFacets().catch(() => null),
      ])
      setRecent(library.items)
      setDashboard(profile)
      setSummary(counts)
      setSuggested(recommended)
      setFacets(index)
      setError(null)

      if (profile) {
        const candidates = shelfCandidates(profile)
        const pages = await Promise.all(
          candidates.map((item) =>
            fetchWorks({ concept: item.features[0].key, page_size: SHELF_SIZE })
              .then((page) => ({
                concept: item.features[0].key,
                label: item.display_name,
                works: page.items,
              }))
              .catch(() => null),
          ),
        )
        setShelves(
          pages.filter((shelf): shelf is Shelf => shelf !== null && shelf.works.length > 0),
        )
      } else {
        setShelves([])
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (session.loading) return
    // Signed out, Home asks the API for nothing at all: the landing is about
    // Noema, and the catalogue is the signed-in experience.
    if (session.account) {
      void loadForReader()
      return
    }
    // Dropped here rather than in the sign-out handler, because signing out
    // is the shell's job now. The anonymous branch renders none of this, but
    // holding one reader's shelves while another signs in would be wrong.
    setRecent([])
    setDashboard(null)
    setSummary(null)
    setShelves([])
    setSuggested(null)
    setFacets(null)
    setLoading(false)
  }, [session.loading, session.account, loadForReader])

  /**
 * Recommended for you.
 *
 * The only shelf on this page that is actually ranked for the reader. The
 * concept shelves below it are a Discover filter and say so; this one
 * excludes what they already hold and orders by their own established
 * preferences.
 *
 * Cold start is an honest sentence, not a filled shelf. There is no
 * popularity model behind this to fall back on, and inventing one to avoid
 * an empty state would be the thing the whole layer is built to refuse.
 */
function RecommendationShelf({
  response,
  onOpenWork,
  onNavigate,
}: {
  response: RecommendationResponse
  onOpenWork: (workId: string) => void
  onNavigate: (view: ProductView) => void
}) {
  // Dismissed here rather than by refetching the shelf: a refetch would slide
  // a replacement into the gap the moment someone clicked, which reads as the
  // page arguing back. The work leaves, the rest stays put.
  const [dismissed, setDismissed] = useState<string[]>([])
  const [dismissing, setDismissing] = useState<string | null>(null)
  const [dismissError, setDismissError] = useState<string | null>(null)

  const dismiss = useCallback(async (workId: string) => {
    setDismissing(workId)
    setDismissError(null)
    try {
      await dismissRecommendation(workId)
      setDismissed((current) => [...current, workId])
    } catch (caught) {
      // Nothing is removed on a failure: a card that vanished without being
      // saved would come back on the next load and look like a bug.
      setDismissError(
        caught instanceof Error ? caught.message : 'Could not save that. Try again.',
      )
    } finally {
      setDismissing(null)
    }
  }, [])

  const { state } = response.summary
  const visible = response.recommendations.filter(
    (item) => !dismissed.includes(item.work.id),
  )
  const prompt =
    state === 'no_activity'
      ? 'Rate a few works to start building your recommendations.'
      : state === 'no_ratings'
        ? 'You have works tracked but none rated yet. Rate a few to start building your recommendations.'
        : state === 'building'
          ? 'Nothing has settled into a pattern yet. Rate a few more works and this fills in.'
          : state === 'no_matches'
            ? 'Nothing new in the catalogue carries the themes you have established yet.'
            : null

  return (
    <section
      aria-labelledby="recommended-heading"
      className="border-b border-paper/10"
    >
      <div className="mx-auto max-w-page px-5 py-9 sm:px-6 md:py-11 lg:px-10">
        <SectionHeading
          id="recommended-heading"
          label="From your ratings"
          title="Recommended for you"
          action={
            prompt
              ? { label: 'Browse the catalogue', onClick: () => onNavigate('discover') }
              : undefined
          }
        />
        <div className="mt-7">
          {dismissError && (
            <div className="mb-8">
              <StateMessage
                kind="error"
                title="That did not save."
                detail={dismissError}
              />
            </div>
          )}
          {prompt ? (
            <StateMessage
              kind="empty"
              title={prompt}
              detail="Noema recommends from what you rate, and says nothing when it has nothing to say."
            />
          ) : visible.length === 0 ? (
            <StateMessage
              kind="empty"
              title="Nothing left on this shelf."
              detail="Rate a few more works and Noema will have more to go on."
            />
          ) : (
            <ul className="rail -mx-5 flex snap-x snap-mandatory gap-6 overflow-x-auto px-5 pb-2 sm:mx-0 sm:grid sm:grid-cols-2 sm:gap-x-8 sm:gap-y-12 sm:overflow-visible sm:px-0 lg:grid-cols-4">
              {visible.map(
                ({ work, user_state: userState, reasons, cautions }: Recommendation) => (
                  <li
                    key={work.id}
                    className="w-[58vw] min-w-[180px] shrink-0 snap-start sm:w-auto"
                  >
                    <p className="mb-3 text-[0.62rem] uppercase tracking-label text-accent">
                      {work.domain.name}
                    </p>
                    <WorkEntry work={work} state={userState} onOpen={onOpenWork} />
                    {reasons[0] && (
                      <div className="mt-3 border-l border-paper/10 pl-3">
                        <p className="text-[0.78rem] leading-relaxed text-paper-dim">
                          {reasonSentence(reasons[0])}
                        </p>
                        <p className="mt-1 text-[0.62rem] uppercase tracking-label text-paper-faint">
                          {supportNote(reasons[0])}
                        </p>
                        {cautions[0] && (
                          <p className="mt-2 text-[0.72rem] leading-relaxed text-paper-faint">
                            {reasonSentence(cautions[0])}
                          </p>
                        )}
                      </div>
                    )}
                    {/* Subtle on purpose. "Not interested" is a small, reversible
                        instruction about this shelf -- not a verdict on the work,
                        and not something to put beside the title. */}
                    <button
                      type="button"
                      disabled={dismissing === work.id}
                      onClick={() => void dismiss(work.id)}
                      className="mt-3 text-[0.62rem] uppercase tracking-label text-paper-faint transition-colors duration-200 hover:text-paper focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
                    >
                      {dismissing === work.id ? 'Saving…' : 'Not interested'}
                    </button>
                  </li>
                ),
              )}
            </ul>
          )}
        </div>
      </div>
    </section>
  )
}

/** Signed in only. Anonymous callers never reach this. */
  const add = useCallback(
    async (workId: string) => {
      setAdding(true)
      try {
        await addToLibrary(workId)
        await loadForReader()
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : String(caught))
      } finally {
        setAdding(false)
      }
    },
    [loadForReader],
  )

  const strongest = dashboard?.strongly_likes[0] ?? dashboard?.mildly_likes[0] ?? null
  const ratedWorks = dashboard?.summary.rated_works ?? 0
  const inProgress = summary?.by_status.in_progress ?? 0
  const rows = tasteRows(dashboard)

  /** Deterministic: the lead work of the strongest shelf. */
  const featureShelf = shelves[0] ?? null
  const feature = featureShelf?.works[0] ?? null

  const standfirst = session.account
    ? inProgress > 0
      ? `${plural(inProgress, 'work is', 'works are')} still open. Noema reads what you rate, and nothing else.`
      : 'Noema reads what you rate, and nothing else. Add something you have finished to begin.'
    : 'Noema reads across literature, anime and manga as one collection — tracking what you read and watch, and describing the themes your ratings keep returning to.'

  return (
    <AppShell
      title={session.account ? 'Welcome back' : 'Noema'}
      masthead={
        session.loading ? undefined : (
          <div className="border-b border-paper/10">
            <Masthead
              heading={session.account ? 'Welcome back' : 'Noema'}
              standfirst={standfirst}
              stats={
                session.account && summary
                  ? [
                      { label: 'In progress', value: inProgress },
                      { label: 'Rated', value: summary.rated },
                    ]
                  : undefined
              }
            />
          </div>
        )
      }
      current="home"
      onNavigate={onNavigate}
      bleed
    >
      {session.error && (
        <div className="mx-auto max-w-page px-5 pt-8 sm:px-6 lg:px-10">
          <StateMessage kind="error" title="Sign-in failed." detail={session.error} />
        </div>
      )}

      {/*
        The signed-out branch is gone.

        Home used to carry a whole anonymous landing page -- a proposition, a
        three-media directory, a sign-in panel -- because Noema was once
        browsable without an account. It is not: the authentication gate in
        `App` sends an anonymous reader to `/login` before this component
        renders, so that branch had become ~200 lines that nothing could
        reach. Its one genuinely useful part, the medium directory, is now a
        section of the signed-in page below, where it has real counts.

        The loading state stays, because `session.loading` is real: the gate
        renders nothing while a stored session is being restored, and this is
        the moment just after, before the reader's own data has arrived.
      */}
      {session.loading || !session.account ? (
        <div className="mx-auto max-w-page px-5 py-16 sm:px-6 lg:px-10">
          <StateMessage kind="loading" title="Checking your session…" />
        </div>
      ) : (
        <>
          {(error || loading) && (
            <div className="mx-auto max-w-page px-5 py-10 sm:px-6 lg:px-10">
              {error && (
                <StateMessage kind="error" title="Something did not load." detail={error} />
              )}
              {loading && <StateMessage kind="loading" title="Loading your Noema…" />}
            </div>
          )}

          {!loading && feature && featureShelf && (
            <Feature
              entry={feature}
              eyebrow={`Carries ${featureShelf.label}`}
              mechanism={`A theme your ratings keep returning to. The same works anyone gets by filtering Discover on ${featureShelf.label}.`}
              onOpenWork={onOpenWork}
              action={
                <button
                  type="button"
                  disabled={adding}
                  onClick={() => void add(feature.work.id)}
                  className="text-[0.85rem] text-paper-dim transition-colors duration-200 hover:text-paper focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50"
                >
                  Add to library
                </button>
              }
            />
          )}

          {/* --- § 02 the active engagement ledger ------------------------ */}
          {!loading && (
            <section aria-labelledby="continue-heading" className="border-b border-paper/10">
              <div className="mx-auto max-w-page px-5 py-9 sm:px-6 md:py-11 lg:px-10">
                <SectionMarker
                  index="02"
                  label="Continue // active engagement ledger"
                  folio={
                    recent.length > 0
                      ? `${recent.length} ${recent.length === 1 ? 'entry' : 'entries'} · most recent first`
                      : undefined
                  }
                />
                <div className="mt-5 flex flex-wrap items-end justify-between gap-4">
                  <h2 id="continue-heading" className="type-headline-lg text-paper">
                    Recent activity
                  </h2>
                  {recent.length > 0 && (
                    <button
                      type="button"
                      onClick={() => onNavigate('library')}
                      className="type-label border-b border-paper/25 pb-1 text-paper-dim transition-colors duration-150 hover:border-accent-bright hover:text-accent-bright focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper"
                    >
                      Your whole library
                    </button>
                  )}
                </div>

                <div className="mt-6">
                  {recent.length === 0 ? (
                    <StateMessage
                      kind="empty"
                      title="Nothing in your library yet."
                      detail="Add something you have read or watched — that is where everything else starts."
                      action={
                        <button
                          type="button"
                          onClick={() => onNavigate('discover')}
                          className="type-label border-b border-accent-bright pb-0.5 text-paper transition-colors duration-150 hover:text-accent-bright focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper"
                        >
                          Find something to add
                        </button>
                      }
                    />
                  ) : (
                    <>
                      <LedgerHead />
                      <ul>
                        {recent.map(({ work, user_state: state }) => (
                          <WorkLedgerRow
                            key={work.id}
                            work={work}
                            state={state}
                            onOpen={onOpenWork}
                          />
                        ))}
                      </ul>
                    </>
                  )}
                </div>
              </div>
            </section>
          )}

          {/* --- recommended for you ------------------------------------- */}
          {!loading && suggested && (
            <RecommendationShelf
              response={suggested}
              onOpenWork={onOpenWork}
              onNavigate={onNavigate}
            />
          )}

          {/* --- one thread, three media --------------------------------- */}
          {!loading &&
            shelves.map((shelf) => (
              <section
                key={shelf.concept}
                aria-labelledby={`shelf-${shelf.concept}`}
                className="border-b border-paper/10 bg-ink/40"
              >
                <div className="mx-auto max-w-page px-5 py-9 sm:px-6 md:py-11 lg:px-10">
                  <SectionMarker
                    index="03"
                    label="Cross-medium exploration"
                    folio={`${shelf.works.length} works carry this theme`}
                  />

                  <div className="mt-6 grid gap-8 lg:grid-cols-[17rem_minmax(0,1fr)] lg:gap-12">
                    <div className="lg:sticky lg:top-28 lg:self-start">
                      <p className="type-label text-accent-bright">One thread, three media</p>
                      <h2
                        id={`shelf-${shelf.concept}`}
                        /*
                          Italic on the heading rather than on the theme name
                          inside it. Wrapping just the name in an <em> puts a
                          second element on the page whose whole text is that
                          name, which makes `getByText(label)` ambiguous
                          against the taste band below -- a real ambiguity for
                          a screen reader too, not only for a test.
                        */
                        className="type-headline-lg mt-4 italic text-paper md:text-[2.6rem] md:leading-[1.08]"
                      >
                        Because you enjoy {shelf.label}
                      </h2>
                      <p className="type-body mt-4 text-paper-dim">
                        Works tagged with this theme. Not ranked for you — the same list
                        anyone gets by filtering Discover on {shelf.label}.
                      </p>
                      <button
                        type="button"
                        onClick={() => onExplore({ concept: shelf.concept })}
                        className="type-label group mt-6 inline-flex items-center gap-2 border border-paper/25 px-4 py-2.5 text-paper transition-colors duration-150 hover:border-accent-bright hover:text-accent-bright focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper"
                      >
                        See all
                        <span
                          aria-hidden="true"
                          className="transition-transform duration-150 group-hover:translate-x-1"
                        >
                          &rarr;
                        </span>
                      </button>
                    </div>

                    {/*
                      The export sets these out as mounted specimens, each in
                      its own hairline cell with the medium named at the head.
                      Straight columns rather than the old stagger: a ruled
                      grid is what makes the three media read as a comparison.
                    */}
                    <ul className="rail -mx-5 flex snap-x snap-mandatory gap-5 overflow-x-auto px-5 pb-2 sm:mx-0 sm:grid sm:grid-cols-2 sm:gap-5 sm:overflow-visible sm:px-0 lg:grid-cols-4">
                      {shelf.works.map(({ work, user_state: state }) => (
                        <li
                          key={work.id}
                          className="w-[58vw] min-w-[180px] shrink-0 snap-start border border-paper/10 bg-ink p-4 transition-colors duration-150 hover:border-paper/25 sm:w-auto"
                        >
                          <p className="type-label mb-3 text-accent-bright">
                            {work.domain.name}
                          </p>
                          <WorkEntry work={work} state={state} onOpen={onOpenWork} />
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              </section>
            ))}

          {/* --- taste, on paper ----------------------------------------- */}
          {!loading && (
            <section
              aria-labelledby="taste-heading"
              /*
                The atmosphere the dark bands carry, weighted for the light
                one. Purely decorative: the base colour underneath is `paper`,
                so nothing here is needed to read the section, and the
                contrast was measured at the worst point the wash reaches
                rather than judged by eye. See `.atmosphere-paper`.
              */
              className="atmosphere-paper grain-paper border-y border-ink/15 text-ink"
            >
              <div className="mx-auto max-w-page px-5 py-10 sm:px-6 md:py-12 lg:px-10">
                <SectionMarker
                  index="04"
                  label="Observed preference"
                  folio={
                    ratedWorks > 0
                      ? `${plural(ratedWorks, 'work rated', 'works rated')}`
                      : undefined
                  }
                  tone="paper"
                />
                <div className="mt-6 grid gap-10 lg:grid-cols-[20rem_minmax(0,1fr)] lg:gap-14">
                  <div>
                    <p className="type-label text-ink-faint">Your taste</p>
                    <h2
                      id="taste-heading"
                      className="type-headline-lg mt-4 max-w-sm text-ink md:text-[2.6rem] md:leading-[1.08]"
                    >
                      What Noema has noticed so far
                    </h2>
                    <p className="type-body mt-4 max-w-sm text-ink-faint">
                      Read from your ratings, nothing else. Your taste takes shape as
                      you rate more.
                    </p>
                    {/*
                      The export repeats its epigraph here and again in the
                      footer. Noema's footer already carries it, and a thesis
                      stated twice on one page reads as decoration rather than
                      as a claim -- so this band points at it instead, and
                      Your Taste is where it is set at scale.
                    */}
                    {ratedWorks > 0 && (
                      <p className="type-body-sm mt-4 text-ink-faint">
                        From {plural(ratedWorks, 'work you have rated', 'works you have rated')}.
                      </p>
                    )}
                    <button
                      type="button"
                      onClick={() => onNavigate('taste')}
                      /*
                        `outline-ink` rather than `outline-accent`: this is
                        the one focusable control on the light band, and
                        accent is 2.64:1 on paper -- under the 3:1 a focus
                        indicator needs. Ink is 6.48:1 at the darkest point
                        the wash behind it reaches.
                      */
                      className="type-label group mt-6 inline-flex items-center gap-2 border border-ink/30 px-4 py-2.5 text-ink transition-colors duration-150 hover:border-accent hover:bg-ink hover:text-band focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
                    >
                      {strongest ? 'View your full taste profile' : 'View your taste profile'}
                      <span
                        aria-hidden="true"
                        className="transition-transform duration-200 group-hover:translate-x-1"
                      >
                        &rarr;
                      </span>
                    </button>
                  </div>

                  {rows.length > 0 ? (
                    <dl className="divide-y divide-ink/15 border-t border-ink/15">
                      {rows.map((row) => (
                        <div
                          key={row.key}
                          className="grid gap-2 py-5 sm:grid-cols-[minmax(0,1fr)_12rem] sm:items-baseline sm:gap-10"
                        >
                          <div>
                            <p className="text-[0.62rem] uppercase tracking-label text-ink-faint">
                              {row.lead}
                            </p>
                            <dt className="mt-2 font-display text-2xl font-light leading-tight md:text-[1.75rem]">
                              {row.name}
                            </dt>
                            <dd className="mt-2 text-[0.85rem] leading-relaxed text-ink-faint">
                              {row.support}
                            </dd>
                          </div>
                          {/*
                            Where a grade used to sit. A reader is told what
                            the reading rests on, not how sure a number is --
                            and on most rows there is nothing to add, so the
                            column is simply empty rather than padded.
                          */}
                          {row.hedge && (
                            <p className="text-[0.8rem] leading-relaxed text-ink-faint sm:pt-1">
                              {row.hedge}
                            </p>
                          )}
                        </div>
                      ))}
                    </dl>
                  ) : (
                    <div className="border-t border-ink/15 pt-7">
                      <p className="font-display text-[1.75rem] font-light leading-[1.2] md:text-[2.25rem]">
                        Keep rating works to build your taste profile.
                      </p>
                      <p className="mt-4 max-w-md text-[0.9rem] leading-relaxed text-ink-faint">
                        {ratedWorks > 0
                          ? `You have rated ${plural(ratedWorks, 'work', 'works')}. Nothing has settled into a preference yet.`
                          : 'Ratings are what tell Noema whether you enjoyed something — finishing it only says you got to the end.'}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            </section>
          )}

          {/* --- § 05 the archival corpus --------------------------------- */}
          {!loading && (
            <section aria-labelledby="corpus-heading" className="border-b border-paper/10">
              <div className="mx-auto max-w-page px-5 py-9 sm:px-6 md:py-11 lg:px-10">
                {/*
                  The export heads this section "Explore the archival corpus".
                  "Corpus" is on Noema's forbidden list for reader-facing copy
                  -- it is what the engineering calls the holding, not what a
                  reader calls it -- and `lib/vocabulary.test.ts` enforces
                  that. The editorial register survives the substitution;
                  the implementation word does not come back.
                */}
                <SectionMarker
                  index="05"
                  label="Explore the collection"
                  folio={`${DOMAINS.length} media`}
                />
                <div className="mt-5 flex flex-wrap items-end justify-between gap-4">
                  <h2 id="corpus-heading" className="type-headline-lg max-w-2xl text-paper">
                    Literature, anime and manga, read together
                  </h2>
                  <button
                    type="button"
                    onClick={() => onNavigate('discover')}
                    className="type-label border-b border-paper/25 pb-1 text-paper-dim transition-colors duration-150 hover:border-accent-bright hover:text-accent-bright focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper"
                  >
                    Explore everything
                  </button>
                </div>

                {/*
                  Counts come from `/works/facets`, which is the same figure
                  Discover filters on -- so a reader who clicks through finds
                  exactly the number they were shown. When the facets request
                  fails the medium is still offered, without a count: an
                  unknown number is not a zero.
                */}
                <ul className="mt-6 grid border-t border-paper/10 md:grid-cols-3">
                  {DOMAINS.map((domain) => {
                    // Optional all the way down: the request can fail, and a
                    // response can arrive without the field. Neither is a
                    // reason to take the directory off the page.
                    const facet = facets?.domains?.find((entry) => entry.value === domain.slug)
                    return (
                      <li
                        key={domain.slug}
                        className="border-b border-paper/10 md:border-b-0 md:border-r md:last:border-r-0"
                      >
                        <button
                          type="button"
                          onClick={() => onExplore({ domain: domain.slug })}
                          className="group flex w-full flex-col gap-2 px-1 py-5 text-left transition-colors duration-150 hover:bg-canvas-soft md:px-6 md:first:pl-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-paper"
                        >
                          <span className="type-label text-accent-bright">
                            {facet ? `${facet.count} in the catalogue` : 'In the catalogue'}
                          </span>
                          <span className="flex items-baseline justify-between gap-4">
                            <span className="font-display text-2xl font-light text-paper transition-colors duration-150 group-hover:text-accent-bright">
                              {domain.label}
                            </span>
                            <span
                              aria-hidden="true"
                              className="text-paper-faint transition-transform duration-150 group-hover:translate-x-0.5"
                            >
                              &rarr;
                            </span>
                          </span>
                        </button>
                      </li>
                    )
                  })}
                </ul>
              </div>
            </section>
          )}
        </>
      )}

      {/*
        A conclusion, not another navigation surface. The thesis is the last
        thing on the page because it is the claim everything above rests on.
      */}
      <footer className="border-t border-paper/10 bg-ink">
        <div className="mx-auto max-w-page px-5 py-9 sm:px-6 md:py-11 lg:px-10">
          <div className="max-w-2xl">
            <p className="font-display text-xl font-light text-paper">Noema</p>
            <p className="mt-5 font-display text-lg font-light italic leading-relaxed text-paper-dim md:text-xl">
              Noema reads your ratings, not your reasons. Everything shown here
              can be traced back to a work you rated.
            </p>
          </div>
        </div>
      </footer>

    </AppShell>
  )
}
