import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import AppShell from '../components/AppShell'
import type { ProductView } from '../components/AppShell'
import SectionHeading from '../components/SectionHeading'
import SignInPrompt from '../components/SignInPrompt'
import StateMessage from '../components/StateMessage'
import WorkEntry from '../components/WorkEntry'
import { fetchWorks } from '../api/catalog'
import { fetchTasteDashboard } from '../api/dashboard'
import { dismissRecommendation, fetchRecommendations } from '../api/recommendations'
import { addToLibrary, fetchLibrary, fetchLibrarySummary } from '../api/library'
import { useSession } from '../auth/session'
import { activityLine } from '../lib/labels'
import { hedge, leadPhrase, supportLine } from '../lib/taste'
import type {
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
    <div className="mx-auto max-w-page px-5 pb-12 pt-14 sm:px-6 md:pb-16 md:pt-20 lg:px-10">
      {/*
        The second column is laid out only when there is something to put in
        it. Reserving it unconditionally left the signed-out masthead with
        half its width empty.
      */}
      <div
        className={`grid gap-8 ${
          stats && stats.length > 0
            ? 'lg:grid-cols-[minmax(0,1fr)_auto] lg:items-end'
            : ''
        }`}
      >
        <div>
          <p className="text-[0.66rem] uppercase tracking-label text-paper-faint">{date}</p>
          <h1 className="mt-5 max-w-3xl font-display text-[2.6rem] font-light leading-[1.05] tracking-tight text-paper sm:text-6xl lg:text-[4.25rem]">
            {heading}
          </h1>
          <p className="mt-6 max-w-xl font-display text-lg font-light leading-relaxed text-paper-dim md:text-xl">
            {standfirst}
          </p>
        </div>

        {/*
          Two counts, read straight from /library/summary. The prototype's
          second figure was "Rated in March"; no month-scoped count is
          exposed, so this is a lifetime total.
        */}
        {stats && stats.length > 0 && (
          <dl className="flex gap-10 border-t border-paper/10 pt-6 lg:border-l lg:border-t-0 lg:pl-10 lg:pt-0">
            {stats.map((stat) => (
              <div key={stat.label}>
                <dt className="text-[0.66rem] uppercase tracking-label text-paper-faint">
                  {stat.label}
                </dt>
                <dd className="mt-2 font-display text-4xl font-light text-paper">
                  {stat.value}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------
 * The signed-out landing
 * ---------------------------------------------------------------------- */

/**
 * Introduces *Noema*, not a work.
 *
 * An earlier pass featured the first work alphabetically, which put a real
 * record with no synopsis and one generic theme in the most prominent
 * position on the page -- an accident of sort order presented as an editorial
 * choice.
 *
 * More than that it is a product boundary: the catalogue and its detail are
 * the signed-in experience. So nothing here names a work, and **this branch
 * issues no API request at all**. There is no work grid padding it out,
 * because a fuller-looking page is not a reason to hand out the thing the
 * boundary exists to hold.
 *
 * What fills the space instead is the same full-bleed cinematic band the
 * prototype used -- the strengthened CSS atmosphere, the scrim, `lg:py-36` --
 * carrying a statement of what Noema does.
 */
function AnonymousHome({
  onNavigate,
  onExplore,
  signInRef,
}: {
  onNavigate: (view: ProductView) => void
  onExplore: (filter: { domain?: string }) => void
  signInRef: React.RefObject<HTMLDivElement | null>
}) {
  return (
    <>
      <section
        aria-labelledby="proposition-heading"
        className="atmosphere grain relative border-y border-paper/10"
      >
        <div aria-hidden="true" className="absolute inset-0 hidden md:block scrim-left" />
        <div aria-hidden="true" className="absolute inset-0 bg-ink/45 md:hidden" />

        <div className="relative mx-auto max-w-page px-5 py-20 sm:px-6 md:py-28 lg:px-10 lg:py-36">
          <div className="max-w-2xl">
            <p className="text-[0.66rem] uppercase tracking-label text-accent">
              A reading and watching notebook
            </p>
            <h2
              id="proposition-heading"
              className="mt-5 font-display text-[2.4rem] font-light leading-[1.08] tracking-tight text-paper sm:text-[3.2rem]"
            >
              Keep what you read and watch in one place, and let the themes come
              out of it.
            </h2>
            <p className="mt-6 max-w-xl font-display text-lg font-light leading-relaxed text-paper/85 md:text-xl">
              Noema is not a rating site and not a recommender. It reads the
              ratings you give and tells you which themes they keep returning
              to &mdash; across a novel, a series and a manga alike.
            </p>

            <div className="mt-10 flex flex-wrap items-center gap-x-8 gap-y-4">
              <button
                type="button"
                onClick={() =>
                  signInRef.current?.scrollIntoView?.({
                    behavior: 'smooth',
                    block: 'center',
                  })
                }
                className="group inline-flex items-center gap-2 border-b border-accent pb-1 text-[0.85rem] text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                Create an account
                <span
                  aria-hidden="true"
                  className="transition-transform duration-200 group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
                >
                  &#8599;
                </span>
              </button>
              <button
                type="button"
                onClick={() => onNavigate('discover')}
                className="text-[0.85rem] text-paper-dim transition-colors duration-200 hover:text-paper focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                Browse the catalogue first
              </button>
            </div>
          </div>
        </div>
      </section>

      <section aria-labelledby="loop-heading" className="border-b border-paper/10">
        <div className="mx-auto max-w-page px-5 sm:px-6 lg:px-10">
          <h2
            id="loop-heading"
            className="pt-16 text-[0.66rem] uppercase tracking-label text-paper-faint md:pt-20"
          >
            How it works
          </h2>
          <ol className="mt-2 grid divide-y divide-paper/10 md:grid-cols-3 md:divide-x md:divide-y-0">
            {[
              {
                n: 'One',
                title: 'Track',
                line: 'Add what you have read or watched, and move it through planned, in progress, completed, on hold or abandoned.',
              },
              {
                n: 'Two',
                title: 'Rate',
                line: 'Finishing something is not the same as liking it, so a rating is its own act. Unrated stays unrated.',
              },
              {
                n: 'Three',
                title: 'See the themes',
                line: 'Noema reads your ratings and names the themes behind them, with the works that support each one.',
              },
            ].map((step) => (
              <li key={step.n} className="md:px-10 md:first:pl-0 md:last:pr-0">
                <div className="flex h-full flex-col py-12">
                  <p className="text-[0.62rem] uppercase tracking-label text-accent">{step.n}</p>
                  <p className="mt-4 font-display text-3xl font-light text-paper">
                    {step.title}
                  </p>
                  <p className="mt-4 max-w-xs text-[0.88rem] leading-relaxed text-paper-dim">
                    {step.line}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section
        aria-labelledby="media-heading"
        className="border-b border-paper/10 bg-surface"
      >
        <div className="mx-auto max-w-page px-5 py-16 sm:px-6 md:py-20 lg:px-10">
          <SectionHeading
            id="media-heading"
            label="Three media, one collection"
            title="Literature, anime and manga, read together"
            description="Held as one collection, so a theme can be followed from a novel into a series without changing tools."
            action={{ label: 'Explore everything', onClick: () => onNavigate('discover') }}
          />
          <ul className="mt-10 grid divide-y divide-paper/10 border-t border-paper/10 md:grid-cols-3 md:divide-x md:divide-y-0">
            {DOMAINS.map((domain) => (
              <li key={domain.slug} className="md:px-10 md:first:pl-0 md:last:pr-0">
                <button
                  type="button"
                  onClick={() => onExplore({ domain: domain.slug })}
                  className="group flex w-full items-baseline justify-between gap-4 py-8 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                >
                  <span className="font-display text-2xl font-light text-paper transition-colors duration-200 group-hover:text-accent">
                    {domain.label}
                  </span>
                  <span
                    aria-hidden="true"
                    className="text-paper-faint transition-transform duration-200 group-hover:translate-x-0.5 group-hover:text-accent"
                  >
                    &rarr;
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section aria-labelledby="signin-heading" ref={signInRef}>
        <div className="mx-auto max-w-page px-5 py-20 sm:px-6 md:py-24 lg:px-10">
          <div className="grid gap-10 lg:grid-cols-[minmax(0,22rem)_minmax(0,1fr)] lg:gap-20">
            <div>
              <p className="text-[0.66rem] uppercase tracking-label text-paper-faint">
                Keep a shelf
              </p>
              <h2
                id="signin-heading"
                className="mt-4 font-display text-3xl font-light leading-[1.1] text-paper md:text-[2.4rem]"
              >
                Track what you read
              </h2>
              <p className="mt-6 max-w-sm text-[0.88rem] leading-relaxed text-paper-dim">
                An account is only needed to keep a library and build a taste
                profile. Browsing is open to everyone.
              </p>
            </div>
            <div className="max-w-md">
              <SignInPrompt
                detail="Keep a library, rate what you finish, and Noema starts naming the themes behind it."
                onLogin={() => onNavigate('login')}
                onRegister={() => onNavigate('register')}
              />
            </div>
          </div>
        </div>
      </section>
    </>
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
  const facts = [work.creators[0]?.name, work.media_format, work.year]
    .filter(Boolean)
    .join(' · ')

  return (
    <section
      aria-labelledby="feature-heading"
      className="atmosphere grain relative border-y border-paper/10"
    >
      {/* Legibility scrim over the wash, so the type keeps its contrast. */}
      <div aria-hidden="true" className="absolute inset-0 hidden md:block scrim-left" />
      <div aria-hidden="true" className="absolute inset-0 bg-ink/45 md:hidden" />

      <div className="relative mx-auto max-w-page px-5 py-20 sm:px-6 md:py-28 lg:px-10 lg:py-36">
        <div className="max-w-xl">
          {/*
            The theme, not "featured artwork". Nothing here claims the
            background depicts this work, because it depicts nothing.
          */}
          <p className="text-[0.66rem] uppercase tracking-label text-accent">{eyebrow}</p>

          <h2
            id="feature-heading"
            className="mt-5 font-display text-[2.6rem] font-light leading-[1.05] tracking-tight text-paper sm:text-[3.4rem]"
          >
            {work.title}
          </h2>

          {work.original_title && work.original_title !== work.title && (
            <p className="mt-2 font-display text-lg font-light italic text-paper-dim">
              {work.original_title}
            </p>
          )}

          {facts && <p className="mt-4 text-[0.85rem] text-paper-dim">{facts}</p>}

          {work.synopsis ? (
            <p className="mt-6 font-display text-lg font-light italic leading-relaxed text-paper/90 md:text-xl">
              {work.synopsis}
            </p>
          ) : (
            // Gutenberg states no description, so literature has none. Said
            // rather than left as a gap in the composition.
            <p className="mt-6 font-display text-lg font-light italic leading-relaxed text-paper-faint">
              No synopsis was supplied with this record.
            </p>
          )}

          {work.concepts.length > 0 && (
            <p className="mt-5 text-[0.82rem] leading-relaxed text-paper-faint">
              {work.concepts
                .slice(0, 4)
                .map((concept) => concept.name)
                .join(' · ')}
            </p>
          )}

          {mechanism && (
            <p className="mt-5 max-w-lg text-[0.8rem] leading-relaxed text-paper-faint">
              {mechanism}
            </p>
          )}

          <div className="mt-9 flex flex-wrap items-center gap-x-8 gap-y-4">
            <button
              type="button"
              onClick={() => onOpenWork(work.id)}
              className="group inline-flex items-center gap-2 border-b border-accent pb-1 text-[0.85rem] text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              Open this work
              <span
                aria-hidden="true"
                className="transition-transform duration-200 group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
              >
                &#8599;
              </span>
            </button>

            {state && state.in_library ? (
              <p className="text-[0.85rem] text-paper-dim">
                In your library
                {state.rating !== null && ` · rated ${state.rating}/10`}
              </p>
            ) : (
              action
            )}
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
  const signInRef = useRef<HTMLDivElement | null>(null)

  const [recent, setRecent] = useState<WorkPresentation[]>([])
  const [dashboard, setDashboard] = useState<TasteDashboard | null>(null)
  const [summary, setSummary] = useState<LibrarySummary | null>(null)
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
      const [library, profile, counts, recommended] = await Promise.all([
        fetchLibrary({ page_size: RECENT_LIMIT }),
        fetchTasteDashboard().catch(() => null),
        fetchLibrarySummary().catch(() => null),
        fetchRecommendations(RECOMMENDATION_LIMIT).catch(() => null),
      ])
      setRecent(library.items)
      setDashboard(profile)
      setSummary(counts)
      setSuggested(recommended)
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
      <div className="mx-auto max-w-page px-5 py-20 sm:px-6 md:py-24 lg:px-10">
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
        <div className="mt-12">
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

      {session.loading ? (
        <div className="mx-auto max-w-page px-5 py-16 sm:px-6 lg:px-10">
          <StateMessage kind="loading" title="Checking your session…" />
        </div>
      ) : !session.account ? (
        /* --- signed out: a public landing, and no work data ------------- */
        <AnonymousHome
          onNavigate={onNavigate}
          onExplore={onExplore}
          signInRef={signInRef}
        />
      ) : (
        /* --- signed in --------------------------------------------------- */
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

          {/* --- still open ---------------------------------------------- */}
          {!loading && (
            <section aria-labelledby="continue-heading" className="border-b border-paper/10">
              <div className="mx-auto max-w-page px-5 py-20 sm:px-6 md:py-24 lg:px-10">
                <SectionHeading
                  id="continue-heading"
                  label="Where you left off"
                  title="Recent activity"
                  action={
                    recent.length > 0
                      ? { label: 'Your whole library', onClick: () => onNavigate('library') }
                      : undefined
                  }
                />

                <div className="mt-12">
                  {recent.length === 0 ? (
                    <StateMessage
                      kind="empty"
                      title="Nothing in your library yet."
                      detail="Add something you have read or watched — that is where everything else starts."
                      action={
                        <button
                          type="button"
                          onClick={() => onNavigate('discover')}
                          className="border-b border-accent pb-0.5 text-[0.85rem] text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                        >
                          Find something to add
                        </button>
                      }
                    />
                  ) : (
                    <ul className="rail -mx-5 flex snap-x snap-mandatory gap-6 overflow-x-auto px-5 pb-2 md:mx-0 md:grid md:grid-cols-5 md:gap-8 md:overflow-visible md:px-0">
                      {recent.map(({ work, user_state: state }) => (
                        <li
                          key={work.id}
                          className="w-[44vw] min-w-[160px] shrink-0 snap-start sm:w-[30vw] md:w-auto"
                        >
                          <WorkEntry
                            work={work}
                            state={state}
                            onOpen={onOpenWork}
                            detail={state ? activityLine(state, work.domain.slug) : null}
                          />
                        </li>
                      ))}
                    </ul>
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
                className="border-b border-paper/10 bg-surface"
              >
                <div className="mx-auto max-w-page px-5 py-20 sm:px-6 md:py-28 lg:px-10">
                  <div className="grid gap-12 lg:grid-cols-[19rem_minmax(0,1fr)] lg:gap-16">
                    <div className="lg:sticky lg:top-28 lg:self-start">
                      <p className="text-[0.66rem] uppercase tracking-label text-paper-faint">
                        One thread, three media
                      </p>
                      <h2
                        id={`shelf-${shelf.concept}`}
                        className="mt-4 font-display text-4xl font-light italic leading-[1.1] text-paper md:text-[2.9rem]"
                      >
                        Because you enjoy {shelf.label}
                      </h2>
                      <p className="mt-6 text-[0.9rem] leading-relaxed text-paper-dim">
                        Works tagged with this theme. Not ranked for you — the same list
                        anyone gets by filtering Discover on {shelf.label}.
                      </p>
                      <button
                        type="button"
                        onClick={() => onExplore({ concept: shelf.concept })}
                        className="group mt-8 inline-flex items-center gap-2 border-b border-paper/25 pb-1 text-[0.82rem] text-paper transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                      >
                        See all
                        <span
                          aria-hidden="true"
                          className="transition-transform duration-200 group-hover:translate-x-1"
                        >
                          &rarr;
                        </span>
                      </button>
                    </div>

                    <ul className="rail -mx-5 flex snap-x snap-mandatory gap-6 overflow-x-auto px-5 pb-2 sm:mx-0 sm:grid sm:grid-cols-2 sm:gap-x-8 sm:gap-y-12 sm:overflow-visible sm:px-0 lg:grid-cols-4">
                      {shelf.works.map(({ work, user_state: state }, index) => (
                        <li
                          key={work.id}
                          className={`w-[58vw] min-w-[180px] shrink-0 snap-start sm:w-auto ${
                            index % 2 === 1 ? 'lg:mt-14' : ''
                          }`}
                        >
                          {/* The medium, called out the way the prototype did. */}
                          <p className="mb-3 text-[0.62rem] uppercase tracking-label text-accent">
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
              <div className="mx-auto max-w-page px-5 py-20 sm:px-6 md:py-28 lg:px-10">
                <div className="grid gap-12 lg:grid-cols-[22rem_minmax(0,1fr)] lg:gap-20">
                  <div>
                    <p className="text-[0.66rem] uppercase tracking-label text-surface">
                      Your taste
                    </p>
                    <h2
                      id="taste-heading"
                      className="mt-4 max-w-sm font-display text-4xl font-light leading-[1.1] md:text-[2.9rem]"
                    >
                      What Noema has noticed so far
                    </h2>
                    <p className="mt-6 max-w-sm text-[0.9rem] leading-relaxed text-surface">
                      Read from your ratings, nothing else. Your taste takes shape as
                      you rate more.
                    </p>
                    {ratedWorks > 0 && (
                      <p className="mt-4 text-[0.85rem] text-surface">
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
                      className="group mt-8 inline-flex items-center gap-2 border-b border-ink/30 pb-1 text-[0.85rem] transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
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
                          className="grid gap-3 py-7 sm:grid-cols-[minmax(0,1fr)_12rem] sm:items-baseline sm:gap-10"
                        >
                          <div>
                            <p className="text-[0.62rem] uppercase tracking-label text-surface">
                              {row.lead}
                            </p>
                            <dt className="mt-2 font-display text-2xl font-light leading-tight md:text-[1.75rem]">
                              {row.name}
                            </dt>
                            <dd className="mt-2 text-[0.85rem] leading-relaxed text-surface">
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
                            <p className="text-[0.8rem] leading-relaxed text-surface sm:pt-1">
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
                      <p className="mt-4 max-w-md text-[0.9rem] leading-relaxed text-surface">
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

        </>
      )}

      {/*
        A conclusion, not another navigation surface. The thesis is the last
        thing on the page because it is the claim everything above rests on.
      */}
      <footer className="border-t border-paper/10 bg-ink">
        <div className="mx-auto max-w-page px-5 py-16 sm:px-6 md:py-20 lg:px-10">
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
