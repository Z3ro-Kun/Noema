import { useCallback, useEffect, useState } from 'react'
import AppShell from '../components/AppShell'
import type { ProductView } from '../components/AppShell'
import RatingControl from '../components/RatingControl'
import StateMessage from '../components/StateMessage'
import StatusControl from '../components/StatusControl'
import { WorkCover } from '../components/WorkCard'
import WorkHistory from '../components/WorkHistory'
import { fetchWork } from '../api/catalog'
import {
  addToLibrary,
  fetchWorkHistory,
  removeFromLibrary,
  updateLibraryEntry,
} from '../api/library'
import { statusLabel } from '../lib/labels'
import type { LibraryHistory, LibraryStatus, WorkPresentation } from '../types/api'

/**
 * One work, and the reader's own relationship with it.
 *
 * Phase 1Y. The product's work page, built on `WorkPresentation` -- canonical
 * `work` plus this caller's `user_state`, two objects rather than one
 * flattened shape. That split is drawn again in the layout: the work's own
 * facts sit in the page body, and anything belonging to the reader sits in a
 * fenced panel beside them. A reader should never have to guess which of the
 * two they are looking at.
 *
 * This is where the product loop closes. Discover finds a work, this page
 * records what the reader did with it, and the preference engine reads that
 * history -- so the controls here are the only place in the product that
 * *writes* the evidence the taste profile is built from.
 *
 * Status and rating stay separate on the way out, as they are in storage:
 * finishing something is not liking it, and a work can be completed and
 * unrated forever. They are two controls, described in their own words, and
 * neither writes the other -- see `StatusControl` and `RatingControl`.
 *
 * Reconsumption needs no concept of its own here. A completed work offers
 * "Read it again", which moves it back to in progress; the backend counts the
 * restart, keeps every earlier completion and leaves the rating alone. The
 * reader never meets the word, and nothing is duplicated.
 *
 * Nothing on this page comes from the internal catalogue record. Adapter
 * names, ingestion provenance, containers, content units and embeddings are
 * the corpus viewer's business, and it is linked as exactly that.
 */

interface WorkPageProps {
  workId: string
  onNavigate: (view: ProductView) => void
  onBack: () => void
  /** The development corpus viewer for this work. */
  onOpenCorpusViewer: (workId: string) => void
  account: string | null
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

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const work = await fetchWork(workId)
      setPresentation(work)
      // History exists only for a work this reader holds, and a 404 there is
      // the normal case rather than a failure, so it never reaches `error`.
      setHistory(
        work.user_state ? await fetchWorkHistory(workId).catch(() => null) : null,
      )
      setError(null)
    } catch (caught) {
      setPresentation(null)
      setHistory(null)
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setLoading(false)
    }
  }, [workId])

  useEffect(() => {
    void load()
  }, [load])

  /**
   * Run one library write and re-read the work.
   *
   * Re-reading rather than patching state locally: the server owns what a
   * status change does to `started_at`, `times_completed` and the rest, and
   * guessing at it here would put two versions of the truth on screen.
   */
  const act = useCallback(
    async (action: () => Promise<unknown>) => {
      setBusy(true)
      try {
        await action()
        const work = await fetchWork(workId)
        setPresentation(work)
        setHistory(
          work.user_state ? await fetchWorkHistory(workId).catch(() => null) : null,
        )
        setActionError(null)
      } catch (caught) {
        setActionError(caught instanceof Error ? caught.message : String(caught))
      } finally {
        setBusy(false)
      }
    },
    [workId],
  )

  const work = presentation?.work ?? null
  const state = presentation?.user_state ?? null

  return (
    <AppShell
      title={work ? work.title : 'Work'}
      subtitle={
        work
          ? [work.domain.name, work.media_format, work.year].filter(Boolean).join(' · ')
          : undefined
      }
      current={null}
      onNavigate={onNavigate}
      actions={
        <button
          type="button"
          onClick={onBack}
          className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
        >
          Back
        </button>
      }
    >
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
              className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-200 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
            >
              Try again
            </button>
          }
        />
      )}

      {work && !loading && (
        <div className="flex flex-col gap-6 lg:flex-row">
          {/* --- canonical: identical for every viewer -------------------- */}
          <div className="min-w-0 flex-1 space-y-6">
            <div className="flex gap-4">
              <WorkCover work={work} />
              <div className="min-w-0">
                {work.original_title && work.original_title !== work.title && (
                  <p className="text-sm text-slate-400">{work.original_title}</p>
                )}
                {work.creators.length > 0 ? (
                  <p className="mt-1 text-sm text-slate-300">
                    {work.creators
                      .map((creator) => `${creator.name} (${creator.role})`)
                      .join(' · ')}
                  </p>
                ) : (
                  <p className="mt-1 text-sm italic text-slate-600">
                    No credits recorded
                  </p>
                )}
                {work.source && (
                  // Attribution is owed. Adapter names and ingestion internals
                  // are not, and are not here.
                  <p className="mt-1 text-xs text-slate-500">
                    Record from {work.source}
                  </p>
                )}
              </div>
            </div>

            <section aria-labelledby="synopsis-heading">
              <h2 id="synopsis-heading" className="text-sm font-medium text-slate-300">
                Synopsis
              </h2>
              {work.synopsis ? (
                <p className="mt-2 text-sm leading-relaxed text-slate-400">
                  {work.synopsis}
                </p>
              ) : (
                <p className="mt-2 text-sm italic text-slate-600">
                  No synopsis available. The source that supplied this record did not
                  include one.
                </p>
              )}
            </section>

            {work.genres.length > 0 && (
              <section aria-labelledby="genres-heading">
                <h2 id="genres-heading" className="text-sm font-medium text-slate-300">
                  Genres
                </h2>
                <p className="mt-1 text-xs text-slate-500">
                  As stated by {work.source ?? 'the source'}.
                </p>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {work.genres.map((genre) => (
                    <span
                      key={genre}
                      className="rounded bg-slate-800 px-2 py-0.5 text-xs text-slate-300"
                    >
                      {genre}
                    </span>
                  ))}
                </div>
              </section>
            )}

            <section aria-labelledby="concepts-heading">
              <h2 id="concepts-heading" className="text-sm font-medium text-slate-300">
                Themes
              </h2>
              <p className="mt-1 text-xs text-slate-500">
                Noema&rsquo;s own vocabulary, shared across every medium. This is what
                your taste profile is built from.
              </p>
              {work.concepts.length > 0 ? (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {work.concepts.map((concept) => (
                    <span
                      key={concept.slug}
                      title={concept.concept_type}
                      className="rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-400"
                    >
                      {concept.name}
                    </span>
                  ))}
                </div>
              ) : (
                <p className="mt-2 text-sm italic text-slate-600">
                  No themes associated with this work yet. That is a gap in
                  Noema&rsquo;s data, not a statement about the work.
                </p>
              )}
            </section>

            <p className="border-t border-slate-800 pt-4 text-xs text-slate-600">
              <button
                type="button"
                onClick={() => onOpenCorpusViewer(work.id)}
                className="underline hover:text-slate-400 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
              >
                Open in the corpus viewer
              </button>{' '}
              — the development surface, with ingestion provenance and the stored
              text.
            </p>
          </div>

          {/* --- this reader's own state, fenced off ---------------------- */}
          <aside
            aria-labelledby="your-state-heading"
            className="w-full shrink-0 space-y-3 lg:w-72"
          >
            <h2 id="your-state-heading" className="text-sm font-medium text-slate-300">
              You and this work
            </h2>

            {!account ? (
              <StateMessage
                kind="empty"
                title="Sign in to track this."
                detail="Adding and rating works is what Noema learns your taste from."
                action={
                  <button
                    type="button"
                    onClick={() => onNavigate('library')}
                    className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm text-slate-200 hover:border-slate-500 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
                  >
                    Sign in
                  </button>
                }
              />
            ) : (
              <div className="space-y-4 rounded-lg border border-slate-700 bg-slate-900/60 p-4">
                {state && state.in_library && (
                  <p className="text-sm text-slate-200">
                    In your library ·{' '}
                    <span className="font-medium">{statusLabel(state.status)}</span>
                  </p>
                )}

                <StatusControl
                  title={work.title}
                  state={state}
                  busy={busy}
                  onAdd={() => void act(() => addToLibrary(work.id))}
                  onStatus={(status: LibraryStatus) =>
                    void act(() => updateLibraryEntry(work.id, { status }))
                  }
                  onRemove={() => void act(() => removeFromLibrary(work.id))}
                />

                {state && state.in_library && (
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

                    {state.times_completed > 1 && (
                      <p className="text-xs text-slate-400">
                        You have finished this {state.times_completed} times.
                      </p>
                    )}

                    {history && <WorkHistory history={history} />}
                  </>
                )}

                {actionError && (
                  <p
                    role="alert"
                    className="rounded-lg border border-red-900 bg-red-950/40 px-3 py-2 text-xs text-red-300"
                  >
                    {actionError}
                  </p>
                )}

                {/*
                  The learning loop, stated once and quietly. Not a claim that
                  anything has been recalculated -- the taste profile is
                  derived per request, and a rating is one more input to it.
                */}
                {state && state.in_library && state.rating !== null && (
                  <p className="text-xs text-slate-500">
                    Your rating helps Noema understand your taste.{' '}
                    <button
                      type="button"
                      onClick={() => onNavigate('taste')}
                      className="underline hover:text-slate-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-slate-300"
                    >
                      See your taste profile
                    </button>
                  </p>
                )}
              </div>
            )}
          </aside>
        </div>
      )}
    </AppShell>
  )
}
