import { useCallback, useEffect, useState } from 'react'
import AppShell from '../components/AppShell'
import type { ProductView } from '../components/AppShell'
import RatingControl from '../components/RatingControl'
import LabelList from '../components/LabelList'
import SignInPrompt from '../components/SignInPrompt'
import StateMessage from '../components/StateMessage'
import StatusControl from '../components/StatusControl'
import WorkHistory from '../components/WorkHistory'
import WorkPlate from '../components/WorkPlate'
import { fetchWork } from '../api/catalog'
import {
  addToLibrary,
  fetchWorkHistory,
  recordReconsumption,
  removeFromLibrary,
  undoReconsumption,
  updateLibraryEntry,
} from '../api/library'
import { relationshipDates } from '../lib/dates'
import { statusLabel } from '../lib/labels'
import type { LibraryHistory, LibraryStatus, WorkPresentation } from '../types/api'
import { DEV_SURFACES } from '../lib/config'

/**
 * One work, and the reader's own relationship with it.
 *
 * Home is an entry, Discover is the corpus, Library is a record. This is the
 * one page about a single work, so it is laid out as an editorial spread
 * rather than as a grid cell or a row: artwork at a size worth looking at,
 * the title as the page's own heading, and the synopsis as the largest body
 * text in the product. The synopsis was taken out of Discover and Library
 * deliberately; this is where it was taken to.
 *
 * ---
 *
 * Canonical and personal stay apart
 *
 * The work's own facts occupy the hero and the metadata band beneath it, and
 * everything belonging to the reader lives in a separate full-width band that
 * says whose it is. That is the split `WorkPresentation` draws in the API --
 * `work` is shared and identical for every viewer, `user_state` belongs to
 * one person -- and a reader should never have to work out which half of a
 * page is which.
 *
 * The rating says "Your rating" in as many words. Noema has no global score,
 * no average, no popularity and no review count, and a bare number beside a
 * work is exactly how a reader would assume otherwise.
 *
 * ---
 *
 * Two kinds of failure, told apart
 *
 * A write and the re-read that follows it used to share one `try`, so a
 * status change that *succeeded* and was then followed by a failed refresh
 * reported itself as a failed status change, over stale values. They are now
 * two phases with two messages: the action failed, or the action worked and
 * the page could not refresh itself. The second is not a reason to tell
 * someone their change was lost.
 *
 * ---
 *
 * Nothing here comes from the internal catalogue record. Adapter names,
 * ingestion provenance, containers, content units and embeddings are the
 * corpus viewer's business, and it is linked as exactly that.
 */

interface WorkPageProps {
  workId: string
  onNavigate: (view: ProductView) => void
  onBack: () => void
  /** The development corpus viewer for this work. */
  onOpenCorpusViewer: (workId: string) => void
  account: string | null
}

/** Credits as one editorial line: "Mary Shelley, author · Gutenberg, source". */
function creditLine(creators: { name: string; role: string }[]): string {
  return creators.map((creator) => `${creator.name} (${creator.role})`).join(' · ')
}

export default function WorkPage({
  workId,
  onNavigate,
  onBack,
  onOpenCorpusViewer,
  account,
}: WorkPageProps) {
  const [presentation, setPresentation] = useState<WorkPresentation | null>(null)
  const [history, setHistory] = useState<LibraryHistory | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  /** A write that landed, followed by a refresh that did not. */
  const [staleError, setStaleError] = useState<string | null>(null)

  /**
   * Read the work, and its history when there is one to read.
   *
   * Three conditions, and each rules out a request that would be made for
   * nobody. There must be a reader -- an anonymous visitor has no history
   * and must send nothing to a library endpoint, whatever the work response
   * happens to contain. There must be an entry. And it must still be held: a
   * soft-removed row does have a history, but nothing on this page renders
   * it.
   *
   * A 404 is the ordinary answer for a work that was never added, so it
   * never reaches `error`.
   */
  const read = useCallback(async (): Promise<WorkPresentation> => {
    const work = await fetchWork(workId)
    setPresentation(work)
    setHistory(
      account && work.user_state && work.user_state.in_library
        ? await fetchWorkHistory(workId).catch(() => null)
        : null,
    )
    return work
  }, [workId, account])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      await read()
      setError(null)
      setStaleError(null)
    } catch (caught) {
      setPresentation(null)
      setHistory(null)
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoading(false)
    }
  }, [read])

  useEffect(() => {
    void load()
  }, [load])

  /**
   * Run one library write, then re-read the work.
   *
   * Re-reading rather than patching state locally: the server owns what a
   * status change does to `started_at`, `times_completed` and the rest, and
   * guessing at it here would put two versions of the truth on screen.
   *
   * The two phases are caught separately. Whether the write landed is the
   * only thing the first `catch` can report, and a refresh that fails
   * afterwards says so in its own words rather than retracting the write.
   */
  const act = useCallback(
    async (action: () => Promise<unknown>) => {
      setBusy(true)
      setActionError(null)
      setStaleError(null)

      try {
        await action()
      } catch (caught) {
        setActionError(caught instanceof Error ? caught.message : String(caught))
        setBusy(false)
        return
      }

      try {
        await read()
      } catch {
        // The change is saved. What is on screen is merely old, and saying
        // "that did not work" here would be false.
        setStaleError(
          'Your change was saved, but Noema could not refresh this page. What you see below may be out of date.',
        )
      } finally {
        setBusy(false)
      }
    },
    [read],
  )

  const work = presentation?.work ?? null
  const state = presentation?.user_state ?? null
  const held = Boolean(state && state.in_library)

  return (
    <AppShell
      title={work ? work.title : 'Work'}
      current={null}
      onNavigate={onNavigate}
      bleed
      actions={
        <button
          type="button"
          onClick={onBack}
          className="border-b border-paper/20 pb-0.5 text-[0.78rem] text-paper-dim transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Back
        </button>
      }
      masthead={
        work ? (
          /* --- the work ------------------------------------------------ */
          <header className="border-b border-paper/10">
            <div className="mx-auto max-w-page px-5 py-14 sm:px-6 md:py-20 lg:px-10">
              <div className="grid gap-10 lg:grid-cols-[22rem_minmax(0,1fr)] lg:gap-16">
                <div className="w-44 sm:w-56 lg:w-full">
                  <WorkPlate work={work} priority />
                </div>

                <div className="min-w-0">
                  <p className="text-[0.66rem] uppercase tracking-label text-paper-faint">
                    {[work.domain.name, work.media_format, work.year]
                      .filter(Boolean)
                      .join(' · ')}
                  </p>

                  <h1 className="mt-5 font-display text-[2.4rem] font-light leading-[1.05] tracking-tight text-paper sm:text-5xl lg:text-[3.4rem]">
                    {work.title}
                  </h1>

                  {work.original_title && work.original_title !== work.title && (
                    <p className="mt-3 font-display text-xl font-light italic text-paper-dim">
                      {work.original_title}
                    </p>
                  )}

                  <p className="mt-6 text-[0.66rem] uppercase tracking-label text-paper-faint">
                    {work.creators.length > 0
                      ? creditLine(work.creators)
                      : 'No credits recorded'}
                  </p>

                  {/*
                    The largest body text in the product, and the reason the
                    other two surfaces no longer carry a synopsis at all.
                  */}
                  <section
                    aria-labelledby="synopsis-heading"
                    className="mt-9 border-t border-paper/10 pt-9"
                  >
                    {/*
                      No visible label: the synopsis is the subject of the
                      page, and captioning it "Synopsis" would be chrome. The
                      region is still named for assistive technology.
                    */}
                    <h2 id="synopsis-heading" className="sr-only">
                      Synopsis
                    </h2>
                    {work.synopsis ? (
                      <p className="max-w-2xl font-display text-lg font-light leading-relaxed text-paper/85 md:text-xl">
                        {work.synopsis}
                      </p>
                    ) : (
                      <p className="max-w-2xl font-display text-lg font-light italic leading-relaxed text-paper-faint">
                        No synopsis available. The source that supplied this record
                        did not include one.
                      </p>
                    )}
                  </section>
                </div>
              </div>
            </div>
          </header>
        ) : (
          <div className="border-b border-paper/10">
            <div className="mx-auto max-w-page px-5 py-14 sm:px-6 lg:px-10">
              <h1 className="font-display text-[2.4rem] font-light text-paper">Work</h1>
            </div>
          </div>
        )
      }
    >
      {(loading || error) && (
        <div className="mx-auto max-w-page px-5 py-14 sm:px-6 lg:px-10">
          {loading && <StateMessage kind="loading" title="Loading this work…" />}
          {error && !loading && (
            <StateMessage
              kind="error"
              title="Noema could not load this work."
              detail={error}
              action={
                <button
                  type="button"
                  onClick={() => void load()}
                  className="border-b border-accent pb-0.5 text-[0.85rem] text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                >
                  Try again
                </button>
              }
            />
          )}
        </div>
      )}

      {work && !loading && (
        <>
          {/* --- what this work is said to be ------------------------- */}
          <section
            aria-labelledby="metadata-heading"
            className="border-b border-paper/10 bg-surface"
          >
            <h2 id="metadata-heading" className="sr-only">
              How this work is classified
            </h2>
            <div className="mx-auto max-w-page px-5 py-14 sm:px-6 md:py-16 lg:px-10">
              <div className="grid gap-12 md:grid-cols-2 md:gap-16 md:divide-x md:divide-paper/10">
                <div className="md:pr-16">
                  <h3 className="text-[0.66rem] uppercase tracking-label text-paper-faint">
                    Themes
                  </h3>
                  <p className="mt-4 max-w-md text-[0.85rem] leading-relaxed text-paper-dim">
                    Noema&rsquo;s own vocabulary, shared across every medium. This is
                    what your taste profile is built from.
                  </p>
                  {work.concepts.length > 0 ? (
                    <LabelList
                      items={work.concepts.map((concept) => concept.name)}
                      noun="themes"
                      limit={6}
                      className="mt-6 font-display text-lg font-light leading-relaxed text-paper"
                    />
                  ) : (
                    <p className="mt-6 font-display text-lg font-light italic text-paper-faint">
                      No themes associated with this work yet. That is a gap in
                      Noema&rsquo;s data, not a statement about the work.
                    </p>
                  )}
                </div>

                <div className="md:pl-16">
                  <h3 className="text-[0.66rem] uppercase tracking-label text-paper-faint">
                    Genres
                  </h3>
                  <p className="mt-4 max-w-md text-[0.85rem] leading-relaxed text-paper-dim">
                    As stated by {work.source ?? 'the source'}. Source labels, kept
                    as given and never merged with Noema&rsquo;s vocabulary.
                  </p>
                  {work.genres.length > 0 ? (
                    <LabelList
                      items={work.genres}
                      noun="genres"
                      limit={6}
                      className="mt-6 font-display text-lg font-light leading-relaxed text-paper"
                    />
                  ) : (
                    <p className="mt-6 font-display text-lg font-light italic text-paper-faint">
                      This source states no genres for this work.
                    </p>
                  )}
                </div>
              </div>
            </div>
          </section>

          {/* --- you and this work ------------------------------------ */}
          <section aria-labelledby="your-state-heading" className="border-b border-paper/10">
            <div className="mx-auto max-w-page px-5 py-14 sm:px-6 md:py-20 lg:px-10">
              <p className="text-[0.66rem] uppercase tracking-label text-paper-faint">
                Yours alone
              </p>
              <h2
                id="your-state-heading"
                className="mt-4 font-display text-3xl font-light leading-[1.1] text-paper md:text-[2.4rem]"
              >
                You and this work
              </h2>

              {!account ? (
                <div className="mt-10">
                  <SignInPrompt
                    detail="Sign in to track this. Adding and rating works is what Noema learns your taste from."
                    onLogin={() => onNavigate('login')}
                    onRegister={() => onNavigate('register')}
                  />
                </div>
              ) : (
                <div className="mt-12 grid gap-12 lg:grid-cols-2 lg:gap-16 lg:divide-x lg:divide-paper/10">
                  {/* --- where you are with it ------------------------- */}
                  <div className="lg:pr-16">
                    <p className="font-display text-2xl font-light text-paper">
                      {held && state
                        ? statusLabel(state.status)
                        : state
                          ? 'Removed from your library'
                          : 'Not in your library'}
                    </p>

                    {state && (
                      <p className="mt-3 text-[0.62rem] uppercase tracking-label text-paper-faint">
                        {relationshipDates(state)
                          .map((date) => `${date.label} ${date.formatted}`)
                          .join(' · ')}
                      </p>
                    )}

                    <div className="mt-8">
                      <StatusControl
                        title={work.title}
                        state={state}
                        domainSlug={work.domain.slug}
                        busy={busy}
                        onAdd={() => void act(() => addToLibrary(work.id))}
                        onStatus={(status: LibraryStatus) =>
                          void act(() => updateLibraryEntry(work.id, { status }))
                        }
                        onRemove={() => void act(() => removeFromLibrary(work.id))}
                        // `act` holds `busy` for the whole round trip and
                        // every control is disabled while it does, so a
                        // second press cannot land before the first has been
                        // counted.
                        onReconsume={() => void act(() => recordReconsumption(work.id))}
                        onUndoReconsume={() =>
                          void act(() => undoReconsumption(work.id))
                        }
                      />
                    </div>
                  </div>

                  {/* --- what you made of it -------------------------- */}
                  <div className="lg:pl-16">
                    {held && state ? (
                      <>
                        <RatingControl
                          title={work.title}
                          rating={state.rating}
                          busy={busy}
                          onRate={(rating) =>
                            void act(() =>
                              updateLibraryEntry(work.id, { rating, rating_set: true }),
                            )
                          }
                        />

                        {/*
                          Stated once and quietly. Not a claim that anything
                          has been recalculated -- the taste profile is
                          derived per request, and a rating is one more input.
                        */}
                        {state.rating !== null && (
                          <p className="mt-6 text-[0.85rem] leading-relaxed text-paper-faint">
                            Your rating helps Noema understand your taste.{' '}
                            <button
                              type="button"
                              onClick={() => onNavigate('taste')}
                              className="border-b border-paper/25 text-paper-dim transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                            >
                              See your taste profile
                            </button>
                          </p>
                        )}

                        {history && (
                          <div className="mt-10 border-t border-paper/10 pt-8">
                            <WorkHistory history={history} />
                          </div>
                        )}
                      </>
                    ) : (
                      <p className="max-w-sm font-display text-lg font-light leading-relaxed text-paper-faint">
                        Rating comes after adding. Noema learns from what you have
                        actually read and watched, so there is nothing to say about
                        this one yet.
                      </p>
                    )}
                  </div>
                </div>
              )}

              {actionError && (
                <div className="mt-10">
                  <StateMessage
                    kind="error"
                    title="That did not work."
                    detail={actionError}
                  />
                </div>
              )}

              {staleError && (
                <div className="mt-10">
                  <StateMessage
                    kind="error"
                    title="Saved, but this page is out of date."
                    detail={staleError}
                    action={
                      <button
                        type="button"
                        onClick={() => void load()}
                        className="border-b border-accent pb-0.5 text-[0.85rem] text-paper transition-colors duration-200 hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                      >
                        Reload this work
                      </button>
                    }
                  />
                </div>
              )}
            </div>
          </section>

          {/* --- where the record came from --------------------------- */}
          <section className="mx-auto max-w-page px-5 py-12 sm:px-6 lg:px-10">
            <p className="max-w-2xl text-[0.8rem] leading-relaxed text-paper-faint">
              {work.source && (
                <>
                  {/*
                    Attribution is owed. Adapter names and ingestion internals
                    are not, and are not here.
                  */}
                  Record from {work.source}.{' '}
                </>
              )}
              {/*
                The record viewer shows stored text and ingestion state, so it
                exists in a development build only -- and so does the link.
                Attribution above it is owed to a reader and stays either way.
              */}
              {DEV_SURFACES && (
                <>
                  <button
                    type="button"
                    onClick={() => onOpenCorpusViewer(work.id)}
                    className="border-b border-paper/25 text-paper-dim transition-colors duration-200 hover:border-accent hover:text-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  >
                    See where this record came from
                  </button>{' '}
                  — a development surface, with the source details and the stored text.
                </>
              )}
            </p>
          </section>
        </>
      )}
    </AppShell>
  )
}
