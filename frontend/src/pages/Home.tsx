import { useCallback, useEffect, useState } from 'react'
import AppShell from '../components/AppShell'
import type { ProductView } from '../components/AppShell'
import SignInPanel from '../components/SignInPanel'
import StateMessage from '../components/StateMessage'
import WorkCard from '../components/WorkCard'
import { activityLine } from '../lib/labels'
import { fetchWorks } from '../api/catalog'
import { fetchTasteDashboard } from '../api/dashboard'
import { fetchLibrary } from '../api/library'
import { useSession } from '../hooks/useSession'
import type {
  TasteDashboard,
  TastePreferenceItem,
  WorkPresentation,
} from '../types/api'

/**
 * Home -- what a reader can do in Noema right now.
 *
 * Phase 1Y replaced the development landing page, which listed the corpus,
 * printed backend health and linked to every debugging surface. That page
 * answered "is the system up". This one answers "what is there for me".
 *
 * Three things for a signed-in reader, in the order they are useful:
 *
 *     Continue        what they most recently touched, straight from their
 *                     own interaction history. Not a recommendation -- it is
 *                     literally what they were last doing.
 *     Your taste      a few lines from the taste profile, with a route to
 *                     the whole thing. Deliberately a preview: duplicating
 *                     the profile here would give two places to maintain the
 *                     same careful wording.
 *     Explore         routes into Discover, including a taste-guided one.
 *
 * ---
 *
 * The taste-guided shelves are not a recommender
 *
 * "Because you enjoy Psychological Depth" is a filter, and the page says so
 * in those words. It takes a concept the profile already established, asks
 * Discover for works carrying it, and shows them in the order Discover
 * returns them. There is no score, no ranking model, nothing persisted and
 * nothing learned -- and a reader can reproduce it exactly by picking the
 * same theme in Discover, which is the test of whether a "for you" shelf is
 * honest.
 *
 * Works already in the reader's library are not hidden from those shelves.
 * Filtering them out would be a small, invisible personalisation, and the
 * card already says when something is held.
 *
 * ---
 *
 * Anonymous readers get a real page
 *
 * Canonical content is shared, and browsing it has never required an
 * account. So the signed-out home explains what Noema is, names the three
 * media it covers, shows some actual works and offers a way in -- rather than
 * a sign-in wall in front of a public corpus.
 */

/** How many recent interactions and shelf works are worth showing at a glance. */
const RECENT_LIMIT = 4
const SHELF_SIZE = 4
/** At most two shelves: three would be a feed, and this is a landing page. */
const SHELF_COUNT = 2

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
  /** The Phase 1O evidence surface, kept reachable for development. */
  onOpenPreferenceEvidence: () => void
}

interface Shelf {
  concept: string
  label: string
  works: WorkPresentation[]
}

/** The established preferences a concept shelf can be built from. */
function shelfCandidates(dashboard: TasteDashboard): TastePreferenceItem[] {
  return (
    [...dashboard.strongly_likes, ...dashboard.mildly_likes]
      // A combination is two concepts, and Discover filters on one. Rather
      // than silently dropping half a finding, pairs are left to the profile
      // page, which can render them properly.
      .filter((item) => item.kind === 'individual' && item.features.length === 1)
      .slice(0, SHELF_COUNT)
  )
}

function AnonymousHome({
  works,
  loading,
  error,
  session,
  onNavigate,
  onOpenWork,
  onExplore,
}: {
  works: WorkPresentation[]
  loading: boolean
  error: string | null
  session: ReturnType<typeof useSession>
  onNavigate: (view: ProductView) => void
  onOpenWork: (workId: string) => void
  onExplore: (filter: { domain?: string }) => void
}) {
  return (
    <div className="space-y-8">
      <section className="space-y-3">
        <p className="max-w-2xl text-sm leading-relaxed text-slate-300">
          Noema reads across literature, anime and manga as one collection. Track
          what you read and watch, rate it, and Noema describes the themes your
          ratings keep pointing at — as a description of your{' '}
          <span className="text-slate-100">taste in stories</span>, never as a
          claim about you.
        </p>
        <div className="flex flex-wrap gap-2">
          {DOMAINS.map((domain) => (
            <button
              key={domain.slug}
              type="button"
              onClick={() => onExplore({ domain: domain.slug })}
              className="rounded-full border border-slate-700 px-3 py-1 text-sm text-slate-300 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
            >
              {domain.label}
            </button>
          ))}
          <button
            type="button"
            onClick={() => onNavigate('discover')}
            className="rounded-full border border-slate-600 bg-slate-800 px-3 py-1 text-sm text-slate-100 hover:border-slate-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
          >
            Explore everything
          </button>
        </div>
      </section>

      <section aria-labelledby="in-noema-heading" className="space-y-3">
        <h2 id="in-noema-heading" className="text-sm font-medium text-slate-300">
          In Noema
        </h2>
        {loading && <StateMessage kind="loading" title="Loading works…" />}
        {error && !loading && (
          <StateMessage kind="error" title="Noema could not load works." detail={error} />
        )}
        {!loading && !error && works.length === 0 && (
          <StateMessage
            kind="empty"
            title="Nothing has been ingested yet."
            detail="Once works are added they will appear here and in Discover."
          />
        )}
        {!loading && works.length > 0 && (
          <ul className="space-y-3">
            {works.map(({ work }) => (
              <li key={work.id}>
                <WorkCard work={work} onOpen={onOpenWork} />
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="signin-heading" className="max-w-md space-y-3">
        <h2 id="signin-heading" className="text-sm font-medium text-slate-300">
          Track what you read
        </h2>
        <p className="text-sm text-slate-400">
          An account is only needed to keep a library and build a taste profile.
          Browsing is open to everyone.
        </p>
        <SignInPanel
          busy={session.busy}
          onSignIn={session.signIn}
          onRegister={session.signUp}
        />
      </section>
    </div>
  )
}

export default function Home({
  onNavigate,
  onOpenWork,
  onExplore,
  onOpenPreferenceEvidence,
}: HomeProps) {
  const session = useSession()

  const [recent, setRecent] = useState<WorkPresentation[]>([])
  const [dashboard, setDashboard] = useState<TasteDashboard | null>(null)
  const [shelves, setShelves] = useState<Shelf[]>([])
  const [sample, setSample] = useState<WorkPresentation[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const loadAnonymous = useCallback(async () => {
    setLoading(true)
    try {
      const page = await fetchWorks({ page_size: RECENT_LIMIT })
      setSample(page.items)
      setError(null)
    } catch (caught) {
      setSample([])
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoading(false)
    }
  }, [])

  const loadForReader = useCallback(async () => {
    setLoading(true)
    try {
      // The library is the only required call. A taste profile that fails to
      // load must not take the page with it -- the reader's own history is
      // still worth showing.
      const [library, profile] = await Promise.all([
        fetchLibrary({ page_size: RECENT_LIMIT }),
        fetchTasteDashboard().catch(() => null),
      ])
      setRecent(library.items)
      setDashboard(profile)
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
        setShelves(pages.filter((shelf): shelf is Shelf => shelf !== null && shelf.works.length > 0))
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
    if (session.account) void loadForReader()
    else void loadAnonymous()
  }, [session.loading, session.account, loadForReader, loadAnonymous])

  const strongest = dashboard?.strongly_likes[0] ?? dashboard?.mildly_likes[0] ?? null
  const ratedWorks = dashboard?.summary.rated_works ?? 0

  return (
    <AppShell
      title={session.account ? 'Welcome back' : 'Noema'}
      subtitle={
        session.account
          ? 'Pick up where you left off, or find something new.'
          : 'Literature, anime and manga, read as one collection.'
      }
      current="home"
      onNavigate={onNavigate}
      actions={
        session.account ? (
          <>
            <p className="hidden text-xs text-slate-500 sm:block">{session.account}</p>
            <button
              type="button"
              onClick={() =>
                void session.signOut().then(() => {
                  setRecent([])
                  setDashboard(null)
                  setShelves([])
                })
              }
              className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
            >
              Log out
            </button>
          </>
        ) : null
      }
    >
      {session.error && (
        <div className="mb-6">
          <StateMessage kind="error" title="Sign-in failed." detail={session.error} />
        </div>
      )}

      {session.loading ? (
        <StateMessage kind="loading" title="Checking your session…" />
      ) : !session.account ? (
        <AnonymousHome
          works={sample}
          loading={loading}
          error={error}
          session={session}
          onNavigate={onNavigate}
          onOpenWork={onOpenWork}
          onExplore={onExplore}
        />
      ) : (
        <div className="space-y-8">
          {error && (
            <StateMessage kind="error" title="Something did not load." detail={error} />
          )}
          {loading && <StateMessage kind="loading" title="Loading your Noema…" />}

          {/* --- continue --------------------------------------------------- */}
          {!loading && (
            <section aria-labelledby="continue-heading" className="space-y-3">
              <div className="flex flex-wrap items-baseline gap-x-3">
                <h2 id="continue-heading" className="text-sm font-medium text-slate-300">
                  Recent activity
                </h2>
                {recent.length > 0 && (
                  <button
                    type="button"
                    onClick={() => onNavigate('library')}
                    className="text-xs text-slate-500 underline hover:text-slate-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
                  >
                    Your whole library
                  </button>
                )}
              </div>

              {recent.length === 0 ? (
                <StateMessage
                  kind="empty"
                  title="Nothing in your library yet."
                  detail="Add something you have read or watched — that is where everything else starts."
                  action={
                    <button
                      type="button"
                      onClick={() => onNavigate('discover')}
                      className="rounded-lg border border-slate-600 bg-slate-800 px-3 py-1.5 text-sm text-slate-100 hover:border-slate-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
                    >
                      Find something to add
                    </button>
                  }
                />
              ) : (
                <ul className="space-y-3">
                  {recent.map(({ work, user_state: state }) => (
                    <li key={work.id}>
                      <WorkCard work={work} state={state} onOpen={onOpenWork}>
                        <p className="text-xs text-slate-400">
                          {state ? activityLine(state, work.domain.slug) : ''}
                        </p>
                      </WorkCard>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          {/* --- taste preview ---------------------------------------------- */}
          {!loading && (
            <section aria-labelledby="taste-heading" className="space-y-3">
              <h2 id="taste-heading" className="text-sm font-medium text-slate-300">
                Your taste
              </h2>

              {strongest ? (
                <div className="rounded-lg border border-slate-800 p-4">
                  <p className="text-slate-200">
                    You particularly enjoy{' '}
                    <span className="font-medium text-slate-100">
                      {strongest.display_name}
                    </span>
                    .
                  </p>
                  <p className="mt-1 text-xs text-slate-500">
                    From {ratedWorks} rated {ratedWorks === 1 ? 'work' : 'works'}.
                  </p>
                  <button
                    type="button"
                    onClick={() => onNavigate('taste')}
                    className="mt-3 rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-200 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
                  >
                    View your full taste profile
                  </button>
                </div>
              ) : (
                <StateMessage
                  kind="empty"
                  title="Keep rating works to build your taste profile."
                  detail={
                    ratedWorks > 0
                      ? `You have rated ${ratedWorks} ${ratedWorks === 1 ? 'work' : 'works'}. Nothing has settled into a preference yet.`
                      : 'Ratings are what tell Noema whether you enjoyed something — finishing it only says you got to the end.'
                  }
                  action={
                    <button
                      type="button"
                      onClick={() => onNavigate('taste')}
                      className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-200 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
                    >
                      View your taste profile
                    </button>
                  }
                />
              )}
            </section>
          )}

          {/* --- taste-guided shelves --------------------------------------- */}
          {!loading &&
            shelves.map((shelf) => (
              <section
                key={shelf.concept}
                aria-labelledby={`shelf-${shelf.concept}`}
                className="space-y-3"
              >
                <div className="flex flex-wrap items-baseline gap-x-3">
                  <h2
                    id={`shelf-${shelf.concept}`}
                    className="text-sm font-medium text-slate-300"
                  >
                    Because you enjoy {shelf.label}
                  </h2>
                  <button
                    type="button"
                    onClick={() => onExplore({ concept: shelf.concept })}
                    className="text-xs text-slate-500 underline hover:text-slate-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
                  >
                    See all
                  </button>
                </div>
                {/*
                  The mechanism, stated. This is a theme filter, not a model:
                  picking the same theme in Discover gives the same works.
                */}
                <p className="text-xs text-slate-600">
                  Works tagged with this theme. Not ranked for you — the same list
                  anyone gets by filtering Discover on {shelf.label}.
                </p>
                <ul className="space-y-3">
                  {shelf.works.map(({ work, user_state: state }) => (
                    <li key={work.id}>
                      <WorkCard work={work} state={state} onOpen={onOpenWork} />
                    </li>
                  ))}
                </ul>
              </section>
            ))}

          {/* --- explore ---------------------------------------------------- */}
          {!loading && (
            <section aria-labelledby="explore-heading" className="space-y-3">
              <h2 id="explore-heading" className="text-sm font-medium text-slate-300">
                Explore
              </h2>
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={() => onNavigate('discover')}
                  className="rounded-lg border border-slate-600 bg-slate-800 px-3 py-1.5 text-sm text-slate-100 hover:border-slate-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
                >
                  All works
                </button>
                {DOMAINS.map((domain) => (
                  <button
                    key={domain.slug}
                    type="button"
                    onClick={() => onExplore({ domain: domain.slug })}
                    className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
                  >
                    {domain.label}
                  </button>
                ))}
              </div>
              <p className="text-xs text-slate-600">
                <button
                  type="button"
                  onClick={onOpenPreferenceEvidence}
                  className="underline hover:text-slate-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
                >
                  Preference evidence
                </button>{' '}
                — the development view of what your ratings say, concept by concept.
              </p>
            </section>
          )}
        </div>
      )}
    </AppShell>
  )
}
